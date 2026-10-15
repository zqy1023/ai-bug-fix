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
from pydantic import ValidationError


def run_stage(
    spec: StepSpec,
    store: RecordStore,
    services: WorkflowServices,
    state: dict[str, Any],
) -> StepResult:
    """执行一个功能阶段并写入 steps.json。已通过则沿用原记录，不覆盖。"""
    run_id = str(state.get("run_id") or "")
    if not run_id:
        raise StepFailed(f"{spec.name} 缺少真实 run_id，拒绝生成调试身份")
    task_id = str(state.get("task_id") or "") if spec.task_level else ""
    if spec.task_level and not task_id:
        raise StepFailed(f"{spec.name} 缺少真实 task_id，拒绝生成调试身份")
    existing = store.load(run_id, spec.name, task_id)
    if existing is not None and existing.status == "succeeded":
        checked = parse_output(spec.name, existing.output)
        output = dump_model(checked)
        return StepResult(
            status="passed",
            output=output,
            next_action=spec.next_step or None,
            audit=StepAudit(
                input_summary=brief_value(existing.input),
                output_summary=brief_value(output),
                elapsed_ms=existing.duration_ms,
            ),
        )
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
    output_payload = dump_model(checked)
    status, business = map_outcome(kind="ok")
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
    return StepResult(
        status="passed",
        output=output_payload,
        next_action=spec.next_step or None,
        audit=StepAudit(
            input_summary=brief_value(record.input),
            output_summary=brief_value(output_payload),
            elapsed_ms=record.duration_ms,
        ),
    )


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
    """单步调试入口。已通过的记录不覆盖。默认沿用它声明的下一步。"""

    def node(state: dict[str, Any]) -> dict[str, Any]:
        result = run_stage(spec, store, services, state)
        action = result.next_action if next_step is None else next_step
        patch = spec.patch(result.output) if result.status == "passed" else {}
        return {
            **patch,
            "last_result": result.status,
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
        record.error = StepError(code=error_code_of(error.code), message=str(error))
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
            reason=str(error),
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