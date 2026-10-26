"""单步执行应用服务。

单步执行严格复用 CLI 调试语义：从本次执行已落盘的记录重建输入、校验前置步骤、
写操作必须显式确认。与 CLI 的唯一区别是增加了运行态校验：活跃执行由 LangGraph
单写者持有，任何旁路写入都会破坏 ``steps.json`` 的一致性。
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from app.application.audit import StepError, StepRecord
from app.application.services import WorkflowServices
from app.domain.models import utc_now
from app.infrastructure.records.lifecycle import LifecycleStore
from app.infrastructure.records.store import RecordStore
from app.infrastructure.runtime import close_sessions
from app.workflow.spec import AgentSpec, PluginSpec

logger = logging.getLogger(__name__)

# 活跃状态由 LangGraph 主图持有写权，禁止旁路单步执行。
ACTIVE_RUN_STATUSES = frozenset(
    {"queued", "running", "pause_requested", "cancel_requested"}
)
# 审批必须走审批接口；retry_prepare 是用户不可见的内部回修节点。
BLOCKED_STEPS = frozenset({"request_approval", "retry_prepare"})


class StepRunnerError(RuntimeError):
    """单步执行被拒绝或失败；``code`` 与 ``status_code`` 供 API 映射。"""

    def __init__(
        self, message: str, *, code: str = "STEP_RUN_REJECTED", status_code: int = 409
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class StepRunRequest:
    """一次单步执行请求。"""

    run_id: str
    task_id: str
    step_name: str
    confirm_write: bool = False
    continue_after: bool = False


def default_store() -> RecordStore:
    """按当前运行配置打开记录仓库。"""
    from app.infrastructure.config import load_config

    return RecordStore(load_config().runtime_dir)


def resolve_spec(name: str) -> PluginSpec:
    """按插件名取契约；步骤名与 agent 短名/全名都接受。"""
    from app.workflow.plugin_loader import (
        BUILTIN_AGENTS,
        BUILTIN_STEPS,
        load_agent,
        load_step,
    )
    from app.workflow.registry import resolve

    short = name[:-6] if name.endswith("_agent") else name
    try:
        if name in BUILTIN_STEPS:
            load_step(name)
            return resolve(name)
        if short in BUILTIN_AGENTS:
            load_agent(short)
            return resolve(f"{short}_agent")
    except Exception as exc:
        raise StepRunnerError(
            f"无法装载步骤 {name}: {exc}", code="STEP_NOT_FOUND", status_code=404
        ) from exc
    raise StepRunnerError(f"未知步骤: {name}", code="STEP_NOT_FOUND", status_code=404)


def dependencies_missing(
    store: RecordStore, run_id: str, spec: PluginSpec, task_id: str
) -> list[str]:
    """返回尚未成功的直接前置步骤名。"""
    missing: list[str] = []
    from app.workflow.definitions import dependencies_for

    dependencies = dependencies_for(
        spec.name, str(store.load_run(run_id).get("review_mode") or "agent")
    )
    for dependency in spec.depends_on if dependencies is None else dependencies:
        dependency_task = task_id if dependency.level == "task" else ""
        record = store.load(run_id, dependency.name, dependency_task)
        if record is None or record.status != "succeeded":
            missing.append(dependency.name)
    return missing


def state_from_records(
    store: RecordStore, run_id: str, task_id: str = ""
) -> dict[str, Any]:
    """只从完整交接产物恢复；展示记录不再反向拼装工作流状态。"""
    from app.infrastructure.config import load_config
    from app.workflow.contracts import Handoff
    from app.workflow.validation import assert_workflow_version

    config = load_config()
    metadata = store.load_run(run_id)
    assert_workflow_version(metadata)
    defaults = {
        "run_id": run_id,
        "task_id": task_id,
        "attempt": 1,
        "workflow_version": metadata["workflow_version"],
        "source_repo_path": metadata.get("source_repo_path", ""),
        "allow_push": bool(metadata.get("allow_push", config.git_push)),
        "require_push": bool(metadata.get("require_push", False)),
        **config.run_parameters(metadata),
    }
    root = store.task_dir(run_id, task_id) if task_id else store.run_dir(run_id)
    if (root / "workflow/state.json").is_file():
        saved = Handoff.model_validate(
            store.read_artifact(run_id, "workflow/state.json", task_id)
        )
        snapshot = saved.state.to_graph()
        if (
            snapshot.get("run_id") != run_id
            or str(snapshot.get("task_id") or "") != task_id
        ):
            raise StepRunnerError("完整交接产物的任务身份不匹配")
        return {**snapshot, **defaults, "attempt": snapshot.get("attempt", 1)}
    if not task_id:
        return defaults
    saved = Handoff.model_validate(
        store.read_artifact(run_id, "workflow/outputs/identify_carrier.json")
    )
    tasks = saved.outcome.output["tasks"]
    task = next((item for item in tasks if item["task_id"] == task_id), None)
    if task is None:
        raise StepRunnerError("完整任务清单中不存在该任务")
    return {**defaults, "tasks": tasks, "task": task, "problem": task["problem"]}


def _require_identity(spec: PluginSpec, run_id: str, task_id: str) -> None:
    if not run_id:
        raise StepRunnerError(
            "必须指定 run_id", code="RUN_ID_REQUIRED", status_code=422
        )
    if spec.task_level and not task_id:
        raise StepRunnerError(
            "任务级插件必须指定 task_id", code="TASK_ID_REQUIRED", status_code=422
        )


def _require_write_confirmation(spec: PluginSpec, confirmed: bool) -> None:
    if spec.requires_confirmation and not confirmed:
        raise StepRunnerError(
            f"{spec.name} 会产生外部写入；请显式确认后再执行",
            code="CONFIRM_WRITE_REQUIRED",
        )


def _archive_step_retry(
    store: RecordStore, run_id: str, task_id: str, spec: PluginSpec
) -> None:
    """Preserve a failed step and its filesystem evidence before replacing it."""
    if not task_id:
        return
    previous = store.load(run_id, spec.name, task_id)
    if previous is None or not (
        previous.status == "failed"
        or str(previous.result or "").upper() in {"FAIL", "FAILED", "REJECTED"}
    ):
        return
    from app.infrastructure.redaction import public_exception
    from app.workflow.retry_context import capture_retry_context

    try:
        path = (
            f"retry/steps/{spec.name}-{previous.attempt}-{uuid.uuid4().hex[:12]}.json"
        )
        store.write_artifact(run_id, path, previous.model_dump(mode="json"), task_id)
        state = state_from_records(store, run_id, task_id)
        state["attempt"] = max(int(state.get("attempt") or 1), previous.attempt)
        state["retry_reason"] = (
            previous.error.message if previous.error else previous.summary
        )
        context = capture_retry_context(state, store, reuse_existing=False)
        if context.get("capture_errors"):
            raise StepRunnerError(
                "上一轮文件证据未能完整保存，保留原步骤和工作区："
                + "; ".join(context["capture_errors"])
            )
    except StepRunnerError:
        raise
    except Exception as error:
        raise StepRunnerError(
            public_exception(error, prefix="保存单步重试证据失败，未覆盖原步骤")
        ) from error


def execute_once(
    spec: PluginSpec,
    *,
    run_id: str,
    task_id: str = "",
    confirm_write: bool = False,
    services: WorkflowServices | None = None,
    continuation: bool = False,
) -> tuple[int, StepRecord | None, dict[str, Any]]:
    """同步执行一个步骤或 Agent，返回 ``(退出码, 步骤记录, 节点结果)``。"""
    _require_identity(spec, run_id, task_id)
    from app.workflow.definitions import REVIEW_EVIDENCE_STEPS
    if spec.name in REVIEW_EVIDENCE_STEPS:
        raise StepRunnerError("该检查已收归修复后代码审查 Agent，请执行 review_agent", code="INTERNAL_REVIEW_STEP")
    _require_write_confirmation(spec, confirm_write)
    store = default_store()
    missing = dependencies_missing(store, run_id, spec, task_id)
    if missing:
        raise StepRunnerError(
            f"{spec.name} 缺少已通过的前置步骤: {', '.join(missing)}",
            code="STEP_DEPENDENCY_MISSING",
        )
    _archive_step_retry(store, run_id, task_id, spec)
    state = state_from_records(store, run_id, task_id)
    state["_single_step"] = True
    if continuation:
        state["attempt"] = (
            max(
                [
                    int(state.get("attempt") or 1),
                    *[
                        record.attempt
                        for record in store.list_steps(run_id, task_id)
                        if record.name.endswith("_agent")
                    ],
                ]
            )
            + 1
        )

    if isinstance(spec, AgentSpec):
        from app.workflow.wiring import build_agent_node

        if spec.kind == "repair":
            evidence = dict(state.get("evidence") or {})
            if not evidence.get("carrier_dir") or not evidence.get("candidates"):
                raise StepRunnerError(
                    "prepare_evidence 未通过或缺少候选文件，不能进入修复"
                )
            if not state.get("baseline_manifest") or not state.get("baseline_dir"):
                raise StepRunnerError("缺少 prepare_source_snapshot 基线，不能进入修复")
        node = build_agent_node(spec, store, services=services)
    else:
        from app.infrastructure.config import load_config
        from app.infrastructure.git.source import sync_source_repo
        from app.workflow.runner import bind
        from app.workflow.wiring import build_services

        if services is None:
            config = load_config()
            services = build_services(
                config=config, source_repo=sync_source_repo(config)
            )
        node = bind(spec, store, services)

    try:
        result = node(state)
    finally:
        close_sessions(run_id, task_id or None)
    record = store.load(run_id, spec.name, task_id if spec.task_level else "")
    payload = result if isinstance(result, dict) else {}
    return (0 if payload.get("last_result") == "passed" else 1), record, payload


class StepRunner:
    """后台单步执行器：先写 running 记录，再异步执行并落终态。"""

    def __init__(
        self,
        store: RecordStore,
        *,
        run_status: Callable[[str], str] | None = None,
        on_finish: Callable[[], None] | None = None,
        on_continue: Callable[[StepRunRequest, dict[str, Any]], None] | None = None,
    ) -> None:
        self._store = store
        self._run_status = run_status or (lambda run_id: "")
        # 由调用方注入运行锁释放动作，保证后台线程结束后不会泄漏锁。
        self._on_finish = on_finish
        self._on_continue = on_continue

    def validate(self, request: StepRunRequest) -> PluginSpec:
        """校验单步执行是否被允许；不通过时抛 :class:`StepRunnerError`。"""
        spec = resolve_spec(request.step_name)
        if spec.name in BLOCKED_STEPS:
            raise StepRunnerError(
                f"{spec.name} 不允许单步执行；审批必须通过审批接口",
                code="STEP_NOT_ALLOWED",
            )
        if not spec.task_level:
            raise StepRunnerError(
                f"{spec.name} 是执行级步骤，不支持在任务内单步执行",
                code="STEP_NOT_TASK_LEVEL",
                status_code=422,
            )
        if not request.task_id:
            raise StepRunnerError(
                "任务级步骤必须指定 task_id", code="TASK_ID_REQUIRED", status_code=422
            )
        if not self._store.run_dir(request.run_id).is_dir():
            raise StepRunnerError(
                "执行记录不存在", code="RUN_NOT_FOUND", status_code=404
            )
        status = self._run_status(request.run_id)
        if status in ACTIVE_RUN_STATUSES:
            raise StepRunnerError(
                f"执行处于 {status}，单步执行会与工作流写冲突；请等待执行结束或先暂停",
                code="RUN_ACTIVE",
            )
        # A failed background resume can leave the persisted approval interrupt pending while
        # the run-level status is FAILED. Do not allow step-level writes to bypass that gate.
        task_steps = self._store.list_steps(request.run_id, request.task_id)
        latest_approval = next(
            (
                record
                for record in reversed(task_steps)
                if record.name == "request_approval"
            ),
            None,
        )
        approval_output = (
            latest_approval.output
            if latest_approval and isinstance(latest_approval.output, dict)
            else {}
        )
        if latest_approval and (
            latest_approval.status in {"waiting", "running"}
            or str(approval_output.get("decision") or "").upper() == "PENDING"
        ):
            raise StepRunnerError(
                "该任务仍处于交付审批中，不能通过单步执行绕过审批；请先在交付审批页完成或恢复决策",
                code="TASK_AWAITING_APPROVAL",
            )
        missing = dependencies_missing(
            self._store, request.run_id, spec, request.task_id
        )
        if missing:
            raise StepRunnerError(
                f"{spec.name} 缺少已通过的前置步骤: {', '.join(missing)}",
                code="STEP_DEPENDENCY_MISSING",
            )
        if spec.requires_confirmation and not request.confirm_write:
            raise StepRunnerError(
                f"{spec.name} 会产生外部写入；请显式确认后再执行",
                code="CONFIRM_WRITE_REQUIRED",
            )
        return spec

    def run(self, request: StepRunRequest) -> dict[str, Any]:
        """校验后立即返回 202 语义结果，实际执行在后台线程完成。"""
        spec = self.validate(request)
        running = self._mark_running(spec, request)
        thread = threading.Thread(
            target=self._execute,
            args=(spec, request),
            name=f"step-run-{request.run_id}-{spec.name}",
            daemon=True,
        )
        thread.start()
        return {
            "run_id": request.run_id,
            "task_id": request.task_id,
            "step": spec.name,
            "status": "running",
            "accepted": True,
            "continue_after": request.continue_after,
            "started_at": running.started_at,
        }

    def _mark_running(self, spec: PluginSpec, request: StepRunRequest) -> StepRecord:
        existing = self._store.load(request.run_id, spec.name, request.task_id)
        _archive_step_retry(self._store, request.run_id, request.task_id, spec)
        record = StepRecord(
            name=spec.name,
            title=spec.title,
            status="running",
            attempt=existing.attempt if existing else 0,
            started_at=utc_now(),
        )
        self._store.save(
            request.run_id, record, request.task_id if spec.task_level else ""
        )
        return record

    def _execute(self, spec: PluginSpec, request: StepRunRequest) -> None:
        continuing = False
        try:
            code, record, payload = execute_once(
                spec,
                run_id=request.run_id,
                task_id=request.task_id,
                confirm_write=request.confirm_write,
                continuation=request.continue_after,
            )
            if (
                request.continue_after
                and self._on_continue is not None
                and record is not None
                and record.status != "failed"
            ):
                continuing = True
                self._on_continue(request, payload)
                return
            if code == 0:
                # Single-step execution does not advance the workflow graph. Leave the task
                # paused for the operator's next step instead of showing it as still running.
                LifecycleStore(self._store.root).update_task(
                    request.run_id,
                    request.task_id,
                    status="paused",
                    current_step=spec.name,
                    attempt=int(record.attempt if record else 0),
                    terminal_step="",
                    terminal_reason="",
                    next_action="单步执行已结束；可继续单步执行后续节点，或通过 Run 控制恢复整轮。",
                    error={},
                )
            else:
                reason = (
                    record.error.message
                    if record and record.error
                    else record.summary
                    if record and record.summary
                    else str(payload.get("failure_reason") or f"{spec.name} 返回失败")
                )
                LifecycleStore(self._store.root).update_task(
                    request.run_id,
                    request.task_id,
                    status="failed",
                    current_step=spec.name,
                    attempt=int(record.attempt if record else 0),
                    terminal_step=spec.name,
                    terminal_reason=reason,
                    next_action="检查本步骤输入、错误和前置依赖后，可在此重新执行本步骤。",
                    error={
                        "code": record.error.code
                        if record and record.error
                        else "STEP_EXECUTION_FAILED",
                        "message": reason,
                    },
                )
            logger.info(
                "单步执行结束 run_id=%s task_id=%s step=%s exit=%s",
                request.run_id,
                request.task_id,
                spec.name,
                code,
            )
        except StepRunnerError as exc:
            if continuing:
                self._continuation_failed(request, str(exc))
                return
            self._write_failure(spec, request, str(exc))
            self._mark_single_step_failed(spec, request, str(exc))
            logger.warning(
                "单步执行被拒绝 run_id=%s task_id=%s step=%s reason=%s",
                request.run_id,
                request.task_id,
                spec.name,
                exc,
            )
        except Exception as exc:
            from app.infrastructure.redaction import public_exception

            message = public_exception(exc, prefix="节点执行异常")
            if continuing:
                self._continuation_failed(request, message)
                return
            self._write_failure(spec, request, message)
            self._mark_single_step_failed(spec, request, message)
            logger.exception(
                "单步执行失败 run_id=%s task_id=%s step=%s",
                request.run_id,
                request.task_id,
                spec.name,
            )
        finally:
            if self._on_finish is not None:
                self._on_finish()

    def _continuation_failed(self, request: StepRunRequest, message: str) -> None:
        """Keep the successful selected step intact when downstream execution fails."""
        LifecycleStore(self._store.root).update_task(
            request.run_id,
            request.task_id,
            status="failed",
            current_step="workflow_continuation",
            terminal_step="workflow_continuation",
            terminal_reason=message,
            next_action="所选节点已完成，请检查后续失败节点并恢复流程。",
            error={"code": "STEP_EXECUTION_FAILED", "message": message},
        )

    def _mark_single_step_failed(
        self, spec: PluginSpec, request: StepRunRequest, message: str
    ) -> None:
        LifecycleStore(self._store.root).update_task(
            request.run_id,
            request.task_id,
            status="failed",
            current_step=spec.name,
            terminal_step=spec.name,
            terminal_reason=message[:1200],
            next_action="检查本步骤输入、错误和前置依赖后，可在此重新执行本步骤。",
            error={"code": "STEP_EXECUTION_FAILED", "message": message[:1200]},
        )

    def _write_failure(
        self, spec: PluginSpec, request: StepRunRequest, message: str
    ) -> None:
        existing = self._store.load(request.run_id, spec.name, request.task_id)
        record = StepRecord(
            name=spec.name,
            title=spec.title,
            status="failed",
            attempt=existing.attempt if existing else 0,
            started_at=existing.started_at if existing else utc_now(),
            finished_at=utc_now(),
            error=StepError(
                code="STEP_EXECUTION_FAILED", message=message[:600], retryable=False
            ),
        )
        self._store.save(
            request.run_id, record, request.task_id if spec.task_level else ""
        )


__all__ = [
    "ACTIVE_RUN_STATUSES",
    "BLOCKED_STEPS",
    "StepRunRequest",
    "StepRunner",
    "StepRunnerError",
    "default_store",
    "dependencies_missing",
    "execute_once",
    "resolve_spec",
    "state_from_records",
]
