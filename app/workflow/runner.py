"""把步骤函数登记成图节点，并写入该执行唯一的 ``steps.json``。

节点不计算路由。它校验本步输入、调用本步函数、校验本步输出、落盘。
每步的输入构造、回写字段由该步骤自己的 ``StepSpec`` 描述，这里不再认识
任何具体步骤。

落盘记录里的 ``status`` 是执行状态、``result`` 是业务结论，两者只由
:func:`app.application.audit.map_outcome` 给出；进程内路由用的 ``last_result``
取值不变。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt
from pydantic import ValidationError

from app.application.audit import (
    ArtifactRef,
    StepError,
    StepRecord,
    brief_value,
    dump_model,
    error_code_of,
    map_outcome,
    summary_of,
)
from app.application.context import RunContext, StepContext, TaskContext
from app.application.contracts import StepAudit, StepResult
from app.application.services import WorkflowServices
from app.domain.approval import APPROVAL_RETRY_TARGETS
from app.domain.errors import StepFailed
from app.domain.models import utc_now
from app.infrastructure.records.store import RecordStore
from app.infrastructure.redaction import public_exception
from app.infrastructure.run_control import cancellation_requested, pause_requested
from app.workflow.handoff import apply_step_result, outcome_of
from app.workflow.routes import (
    CLAIM,
    DELIVER,
    PUBLISH,
    PREPARE_CONTEXT,
    REVIEW,
    REPAIR,
    REPRODUCE,
)
from app.workflow.spec import StepSpec


def run_stage(
    spec: StepSpec,
    store: RecordStore,
    services: WorkflowServices,
    state: dict[str, Any],
) -> StepResult:
    """执行一个功能阶段并写入 steps.json；显式重跑时重新执行节点。"""
    run_id = str(state.get("run_id") or "")
    if not run_id:
        raise StepFailed(f"{spec.name} 缺少真实 run_id，拒绝生成调试身份")
    from app.workflow.validation import assert_workflow_version

    assert_workflow_version(store.load_run(run_id))
    task_id = str(state.get("task_id") or "") if spec.task_level else ""
    if spec.task_level and not task_id:
        raise StepFailed(f"{spec.name} 缺少真实 task_id，拒绝生成调试身份")
    # 不按步骤名复用历史成功输出。LangGraph checkpoint 负责恢复已完成节点；
    # 重新显式调用节点（尤其单步调试、重试和验证链）时必须重新执行，
    # 否则工作区或上游证据变化后可能拿到过期的验证/回归结果。
    started = time.perf_counter()
    previous_record = store.load(run_id, spec.name, task_id)
    previous_attempt = previous_record.attempt if previous_record else 0
    record = StepRecord(
        name=spec.name,
        title=spec.title,
        status="running",
        started_at=utc_now(),
        attempt=max(int(state.get("attempt") or 1), int(previous_attempt or 0) + 1),
    )
    # 本次节点执行的产物收集范围；重试不会带上一次尝试的引用。
    artifacts: list[ArtifactRef] = []
    try:
        # 暂停包括 FINALIZE 在内的节点边界；取消则必须放行 FINALIZE 生成终态报告。
        if pause_requested(store.root, run_id) and not state.get("_single_step"):
            interrupt({"control": "pause", "run_id": run_id, "step": spec.name})
        if spec.name != "finalize_execution" and cancellation_requested(
            store.root, run_id
        ):
            raise StepFailed(
                "收到取消请求，已在安全节点边界停止后续执行",
                result="manual",
                code="RUN_CANCEL_REQUESTED",
                output={"control_cancelled": True},
            )
        parsed = spec.input_model.model_validate(spec.input_builder(state))
        record.input = dump_model(parsed)
        # 长步骤（证据准备、回归、交付）可能执行数十秒；先落 running 记录，
        # 工作台才能在执行期间展示“执行中”，而不是一直停留在待执行。
        store.save(run_id, record, task_id, **_views(spec))
        run_context = RunContext(
            run_id=run_id,
            started_at=record.started_at,
            source_environment=_optional_text(state.get("source_environment")),
            config_version=_optional_text(state.get("config_version")),
            operator=_optional_text(state.get("operator")),
            audit_id=_optional_text(state.get("audit_id")),
        )
        context = StepContext(
            run=run_context,
            task=_task_context(state, task_id) if task_id else None,
            store=store,
            services=services,
            artifacts=artifacts,
        )
        output = spec.runner(context, parsed)
        artifacts = context.artifacts
        checked = spec.output_model.model_validate(dump_model(output))
    except GraphInterrupt:
        record.status = "waiting"
        record.artifacts = list(artifacts)
        record.finished_at = utc_now()
        record.duration_ms = round((time.perf_counter() - started) * 1000, 2)
        store.save(run_id, record, task_id, **_views(spec))
        raise
    except StepFailed as error:
        return _save_fail(
            spec,
            store,
            state,
            record,
            task_id,
            artifacts,
            started,
            error,
            **_views(spec),
        )
    except (ValidationError, KeyError, ValueError) as error:
        # 输入/输出契约错误属于当前步骤失败，必须写入步骤记录并由路由处理；
        # 该分支必须位于通用异常捕获之前，否则永远不会执行。
        return _save_fail(
            spec,
            store,
            state,
            record,
            task_id,
            artifacts,
            started,
            StepFailed(str(error)),
            **_views(spec),
        )
    except Exception as error:
        # Step plugins must fail inside the task graph, not escape the nested subgraph
        # and abort every carrier in the run. The router decides whether to retry or finalize.
        reason = public_exception(error, prefix=f"步骤 {spec.name} 执行异常")
        return _save_fail(
            spec,
            store,
            state,
            record,
            task_id,
            artifacts,
            started,
            StepFailed(
                reason,
                result="failed",
                code=error_code_of("system_crash"),
                output={"failure_reason": reason},
            ),
            **_views(spec),
        )
    output_payload = dump_model(checked)
    status, business = map_outcome(
        kind="ok",
        output=output_payload,
        output_model=spec.output_model,
    )
    record.status = status
    record.result = business
    record.summary = summary_of(output_payload)
    record.artifacts = list(artifacts)
    record.output = output_payload
    record.finished_at = utc_now()
    record.duration_ms = round((time.perf_counter() - started) * 1000, 2)
    formal = outcome_of(
        record.output,
        execution_status="failed" if record.status == "failed" else "succeeded",
        error={
            "code": str(record.error.code),
            "message": record.error.message,
            "retryable": True,
        }
        if record.error
        else None,
        summary=record.summary,
        business=record.result,
    )
    record.execution, record.assessment = formal.execution, formal.assessment
    try:
        store.save(run_id, record, task_id, **_views(spec))
    except StepFailed as error:
        # 写不进去就是这一步失败，不能留下一条"执行成功"的记录。
        return _save_fail(
            spec,
            store,
            state,
            record,
            task_id,
            artifacts,
            started,
            error,
            **_views(spec),
        )
    return StepResult(
        outcome=outcome_of(
            output_payload,
            summary=record.summary,
            business=business,
            artifacts=[item.path for item in artifacts],
        ),
        audit=StepAudit(
            input_summary=brief_value(record.input),
            output_summary=brief_value(output_payload),
            elapsed_ms=record.duration_ms,
        ),
    )


def _retry_target_for_step(step_name: str) -> str:
    """Map an atomic plugin failure to its owning graph phase for bounded task retry."""
    if step_name in {
        "prepare_source_snapshot",
        "repair_branch_preflight",
        "prepare_workspace",
        "prepare_evidence",
    }:
        return PREPARE_CONTEXT
    if step_name in {"check_changes", "syntax_regression", "verify_issue"}:
        return REPAIR
    if step_name == "run_regression":
        return REVIEW
    if step_name == "prepare_delivery":
        return DELIVER
    if step_name in {PUBLISH, REPRODUCE}:
        return step_name
    if step_name == "claim_repair_slot":
        return CLAIM
    return ""


def _failure_route_patch(result: StepResult) -> dict[str, Any]:
    """仅将 Agent 失败所需的路由元数据写回图状态。"""
    if result.failure is None:
        return {}
    target = str(result.output.get("retry_target") or "")
    if target not in {"locate_agent", "repair_agent", "review_agent"}:
        return {}
    reason = str(result.output.get("failure_reason") or result.failure.reason)
    return {"retry_target": target, "retry_reason": reason[:2000]}


def _views(spec: StepSpec) -> dict[str, Any]:
    """落盘投影声明。运行时数据流不受影响。"""
    return {"input_view": spec.input_record, "output_view": spec.output_record}



def bind(
    spec: StepSpec,
    store: RecordStore,
    services: WorkflowServices,
    *,
    next_step: str | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """单步调试入口。每次显式调用都会执行步骤；默认沿用声明的下一步。"""

    def node(state: dict[str, Any]) -> dict[str, Any]:
        run_id = str(state.get("run_id") or "")
        # 若取消发生在最终收尾前，必须让 final.json 也记录 CANCELLED，而不是覆盖成 SUCCESS。
        if spec.name == "finalize_execution" and cancellation_requested(
            store.root, run_id
        ):
            state = {**state, "terminal_status": "CANCELLED"}
        result = run_stage(spec, store, services, state)
        cancelled = spec.name != "finalize_execution" and cancellation_requested(
            store.root, run_id
        )
        from app.workflow.definitions import next_step_for

        # 审批拒绝是业务决定而非节点崩溃；保留 REJECT 决定供路由进入有上限的回修循环。
        if result.failure is None and not (
            spec.name == "request_approval"
            and result.output.get("decision") == "REJECT"
        ):
            patch = spec.patch(result.output)
            route_status = str(patch.get("last_result") or result.status)
            # A business FAIL is an ordinary node return, so LangGraph RetryPolicy cannot
            # see it. Convert only explicitly retryable phases into bounded graph retries.
            target = str(patch.get("retry_target") or "") or (
                _retry_target_for_step(spec.name) if route_status == "failed" else ""
            )
            patch["retry_target"] = target
            if target:
                patch["retry_reason"] = (
                    summary_of(result.output) or f"{spec.name} 返回失败结论"
                )
        elif (
            spec.name == "request_approval"
            and str(result.output.get("decision") or "").upper() == "REJECT"
        ):
            # Only a verified rejection with successful branch cleanup may enter the retry loop.
            # A cleanup failure, invalid retry target, or manually blocked approval must stop;
            # silently defaulting an invalid target to repair_agent can rerun the wrong stage.
            patch = spec.patch(result.output)
            target = str(result.output.get("retry_target") or "")
            allowed_targets = APPROVAL_RETRY_TARGETS
            retry_reason = str(
                result.output.get("retry_reason")
                or result.output.get("reason")
                or "人工打回"
            )[:2000]
            if (
                result.status == "manual"
                or bool(result.output.get("approval_cleanup_failed"))
                or target not in allowed_targets
            ):
                patch["approval_recovery_blocked"] = True
                patch["retry_target"] = ""
                patch["retry_reason"] = retry_reason
            else:
                patch["approval_recovery_blocked"] = False
                patch["retry_target"] = target
                patch["retry_reason"] = retry_reason
        else:
            # 只对明确可恢复的原子步骤设置任务子图的有界重试目标。
            patch = _failure_route_patch(result)
            if spec.name in {"verify_issue", "run_regression"}:
                payload = {"passed": False, "status": "unverified", **result.output}
                if "reason" not in payload:
                    payload["reason"] = result.failure.reason
                patch.update(spec.patch(payload))
            if result.failure is not None and result.failure.recoverable:
                target = _retry_target_for_step(spec.name)
                if target:
                    patch.update(
                        {
                            "retry_target": target,
                            "retry_reason": str(
                                result.failure.reason or "步骤执行失败"
                            )[:2000],
                        }
                    )
            # 交付失败必须留下状态，但 after_delivery 会先进行有界重试。
            if spec.name in {DELIVER, PUBLISH}:
                key = "delivery_candidate" if spec.name == "prepare_delivery" else "delivery"
                patch = {**patch, key: {"delivery_status": "FAILED"}}

        routed_state = {**state, **patch, "last_result": patch.get("last_result", result.status)}
        action = next_step_for(spec.name, routed_state) if next_step is None else next_step
        if cancelled:
            action = "finalize_execution"
        patch = {
            **patch,
            "last_result": "cancelled"
            if cancelled
            else patch.get("last_result", result.status),
            "terminal_status": "CANCELLED"
            if cancelled
            else patch.get("terminal_status", state.get("terminal_status", "")),
            "next_step": action,
        }
        return apply_step_result(store, state, spec.name, patch, result.outcome)

    node.__name__ = spec.name
    return node


def _save_fail(
    spec: StepSpec,
    store: RecordStore,
    state: dict[str, Any],
    record: StepRecord,
    task_id: str,
    artifacts: list[ArtifactRef],
    started: float,
    error: StepFailed,
    **views: Any,
) -> StepResult:
    """失败同样写入 steps.json。执行状态与业务结论都走唯一映射。"""
    output = {
        key: value
        for key, value in error.output.items()
        if not str(key).startswith("_")
    }
    status, business = map_outcome(
        kind="failed",
        step_result=error.result,
        output=output,
        output_model=spec.output_model,
    )
    if (
        (
            spec.name in {"verify_issue", "run_regression"}
            and output.get("status") == "failed"
        )
        or (
            spec.name == "check_changes"
            and "checks" in output
            and output.get("passed") is False
        )
        or error.code == "SYNTAX_INVALID"
    ):
        status, business = "succeeded", "FAIL"
    record.status = status
    record.result = business
    record.summary = summary_of(output) or public_exception(
        error, prefix=f"步骤 {spec.name}"
    )
    record.artifacts = list(artifacts)
    record.output = output
    if status == "failed":
        record.error = StepError(
            code=error_code_of(error.code),
            message=public_exception(error, prefix=f"步骤 {spec.name} 执行失败"),
        )
    record.finished_at = utc_now()
    record.duration_ms = round((time.perf_counter() - started) * 1000, 2)
    formal = outcome_of(
        record.output,
        execution_status="failed" if record.status == "failed" else "succeeded",
        error={
            "code": str(record.error.code),
            "message": record.error.message,
            "retryable": True,
        }
        if record.error
        else None,
        summary=record.summary,
        business=record.result,
    )
    record.execution, record.assessment = formal.execution, formal.assessment
    store.save(str(state.get("run_id") or ""), record, task_id, **views)
    # 进程内路由取值保持不变：manual 仍是 manual，rejected 仍落 failed。
    return StepResult(
        outcome=outcome_of(
            record.output,
            execution_status="failed" if status == "failed" else "succeeded",
            error={
                "code": error.code or "STEP_EXECUTION_FAILED",
                "message": record.summary,
                "retryable": error.result == "failed",
            }
            if status == "failed"
            else None,
            summary=record.summary,
            business=business,
            artifacts=[item.path for item in artifacts],
        ),
        audit=StepAudit(
            input_summary=brief_value(record.input),
            output_summary=brief_value(record.output),
            elapsed_ms=record.duration_ms,
        ),
    )


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _task_context(state: dict[str, Any], task_id: str) -> TaskContext:
    task = state.get("task")
    if not isinstance(task, dict):
        raise StepFailed("任务级步骤缺少 identify_carrier 生成的 task")
    problem = task.get("problem")
    problem = problem if isinstance(problem, dict) else {}
    evidence = state.get("evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    carrier = _required_text(task, "carrier")
    service_name = _required_text(task, "service_name")
    biztype = _required_text(task, "biztype")
    return TaskContext(
        task_id=task_id,
        service_name=service_name,
        biztype=biztype,
        carrier=carrier,
        error_fingerprint=_required_text(task, "problem_key"),
        framework="spiders"
        if evidence.get("legacy_only")
        else "gathers"
        if evidence
        else None,
        source_repo=_required_text(state, "source_repo_path"),
        workspace_path=_optional_text(state.get("ai_workspace_path")),
        baseline_manifest=(
            {str(key): str(value) for key, value in state["baseline_manifest"].items()}
            if isinstance(state.get("baseline_manifest"), dict)
            else None
        ),
        evidence_refs=("evidence",) if evidence else (),
        attempt=int(state.get("attempt") or 1),
    )


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise StepFailed(f"任务上下文缺少有效 {field}")
    return value


__all__ = ["StepFailed", "bind", "run_stage"]
