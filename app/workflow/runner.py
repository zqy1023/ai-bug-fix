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

from app.domain.models import utc_now
from app.infrastructure.records.store import RecordStore
from app.infrastructure.run_control import cancellation_requested, pause_requested
from app.infrastructure.redaction import public_exception
from app.application.audit import (
    ArtifactRef,
    StepError,
    StepRecord,
    brief_value,
    dump_model,
    error_code_of,
    map_outcome,
    parse_input,
    parse_output,
    summary_of,
)
from app.application.contracts import StepAudit, StepFailure, StepResult
from app.application.services import WorkflowServices
from app.application.context import RunContext, StepContext, TaskContext
from app.workflow.spec import StepSpec
from app.domain.errors import StepFailed
from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt
from pydantic import ValidationError


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
    task_id = str(state.get("task_id") or "") if spec.task_level else ""
    if spec.task_level and not task_id:
        raise StepFailed(f"{spec.name} 缺少真实 task_id，拒绝生成调试身份")
    # 不按步骤名复用历史成功输出。LangGraph checkpoint 负责恢复已完成节点；
    # 重新显式调用节点（尤其单步调试、重试和验证链）时必须重新执行，
    # 否则工作区或上游证据变化后可能拿到过期的验证/回归结果。
    started = time.perf_counter()
    record = StepRecord(
        name=spec.name,
        title=spec.title,
        status="running",
        started_at=utc_now(),
        attempt=int(state.get("attempt") or 1),
    )
    # 本次节点执行的产物收集范围；重试不会带上一次尝试的引用。
    artifacts: list[ArtifactRef] = []
    try:
        # 暂停包括 FINALIZE 在内的节点边界；取消则必须放行 FINALIZE 生成终态报告。
        if pause_requested(store.root, run_id):
            interrupt({"control": "pause", "run_id": run_id, "step": spec.name})
        if spec.name != "finalize_execution" and cancellation_requested(store.root, run_id):
            raise StepFailed(
                "收到取消请求，已在安全节点边界停止后续执行",
                result="manual",
                code="RUN_CANCEL_REQUESTED",
                output={"control_cancelled": True},
            )
        parsed = parse_input(spec.name, spec.input_builder(state))
        record.input = dump_model(parsed)
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
        )
        output = spec.runner(context, parsed)
        artifacts = context.artifacts
        checked = parse_output(spec.name, dump_model(output))
    except GraphInterrupt:
        record.status = "waiting"
        record.artifacts = list(artifacts)
        record.finished_at = utc_now()
        record.duration_ms = round((time.perf_counter() - started) * 1000, 2)
        store.save(run_id, record, task_id, **_views(spec))
        raise
    except StepFailed as error:
        return _save_fail(spec, store, state, record, task_id, artifacts, started, error, **_views(spec))
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
        # Agent SDK/API/进程级异常不能直接冒泡出任务子图；按 Agent 执行失败记录，
        # 由 after_* 路由进入有界 retry，耗尽后只结束当前 task。
        if spec.name in {"locate_agent", "repair_agent", "review_agent"}:
            return _save_fail(
                spec,
                store,
                state,
                record,
                task_id,
                artifacts,
                started,
                StepFailed(
                    public_exception(error, prefix=f"{spec.name} 执行异常"),
                    result="failed",
                    code="AGENT_EXECUTION_ERROR",
                    output={
                        "retry_target": spec.name,
                        "failure_reason": public_exception(error, prefix="Agent 执行失败"),
                    },
                ),
                **_views(spec),
            )
        raise
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
    try:
        store.save(run_id, record, task_id, **_views(spec))
    except StepFailed as error:
        # 写不进去就是这一步失败，不能留下一条"执行成功"的记录。
        return _save_fail(spec, store, state, record, task_id, artifacts, started, error, **_views(spec))
    route_status = _route_status(business)
    return StepResult(
        status=route_status,
        output=output_payload,
        next_action=spec.next_step or None,
        audit=StepAudit(
            input_summary=brief_value(record.input),
            output_summary=brief_value(output_payload),
            elapsed_ms=record.duration_ms,
        ),
    )


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


def _route_status(business: str | None) -> str:
    """将业务结论映射为图内路由状态；不改变持久化的节点执行状态。"""
    if business in {"MANUAL", "INSUFFICIENT_EVIDENCE"}:
        return "manual"
    if business in {"FAIL", "FAILED", "REJECTED"}:
        return "failed"
    if business == "SKIPPED":
        return "skipped"
    return "passed"


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
        if spec.name == "finalize_execution" and cancellation_requested(store.root, run_id):
            state = {**state, "terminal_status": "CANCELLED"}
        result = run_stage(spec, store, services, state)
        cancelled = spec.name != "finalize_execution" and cancellation_requested(store.root, run_id)
        action = result.next_action if next_step is None else next_step
        if cancelled:
            action = "finalize_execution"
        # 审批拒绝是业务决定而非节点崩溃；保留 REJECT 决定供路由进入有上限的回修循环。
        if result.failure is None or (
            spec.name == "request_approval" and str(result.output.get("decision") or "").upper() == "REJECT"
        ):
            # 业务 FAIL/MANUAL 仍需把结构化结论回写路由状态；只有执行异常才丢弃普通输出。
            patch = spec.patch(result.output)
            # 清除上一次 Agent 异常留下的目标，防止后续验证失败误重试旧 Agent。
            patch["retry_target"] = ""
        else:
            # Agent 调用异常时只回写白名单内的重试元数据，不把异常输出当成业务结果。
            # 否则 after_retry 看不到 retry_target，定位失败会错误地回到 repair_agent。
            patch = _failure_route_patch(result)
            # 交付失败必须留下状态。终态之后会按审查通过再次进入交付，
            # 不标记的话，一个 git 异常会在当前任务里来回打转。
            if spec.name == "prepare_delivery":
                patch = {**patch, "delivery": {"delivery_status": "FAILED"}}

        return {
            **patch,
            "last_result": "cancelled" if cancelled else result.status,
            "terminal_status": "CANCELLED" if cancelled else state.get("terminal_status", ""),
            "next_step": action,
            "step_result": result.model_dump(mode="json"),
        }

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
    output = {key: value for key, value in error.output.items() if not str(key).startswith("_")}
    status, business = map_outcome(
        kind="failed",
        step_result=error.result,
        output=output,
        output_model=spec.output_model,
    )
    record.status = status
    record.result = business
    record.summary = summary_of(output)
    record.artifacts = list(artifacts)
    record.output = output
    if status == "failed":
        record.error = StepError(
            code=error_code_of(error.code),
            message=public_exception(error, prefix=f"步骤 {spec.name} 执行失败"),
        )
    record.finished_at = utc_now()
    record.duration_ms = round((time.perf_counter() - started) * 1000, 2)
    store.save(str(state.get("run_id") or ""), record, task_id, **views)
    # 进程内路由取值保持不变：manual 仍是 manual，rejected 仍落 failed。
    result_status = "skipped" if error.result == "skipped" else "manual" if error.result == "manual" else "failed"
    return StepResult(
        status=result_status,
        output=record.output,
        failure=StepFailure(
            code=error.result,
            reason=public_exception(error, prefix=f"步骤 {spec.name} 执行失败"),
            recoverable=error.result == "failed",
        ),
        next_action=spec.next_step or None,
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
        framework="spiders" if evidence.get("legacy_only") else "gathers" if evidence else None,
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
