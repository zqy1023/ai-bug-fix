"""步骤执行与审计记录调度 (Step Execution & Plugin Runner)。

整合运行时服务契约、插件注册发现、静态契约自检、节点绑定与单步执行服务。
"""

from __future__ import annotations

import importlib
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt
from pydantic import BaseModel, ValidationError

from config.settings import settings
from core import APPROVAL_RETRY_TARGETS, utc_now
from core.contracts import (
    AgentSpec,
    ArtifactRef,
    PluginSpec,
    RunContext,
    StepAudit,
    StepContext,
    StepError,
    StepRecord,
    StepResult,
    StepSpec,
    TaskContext,
    brief_value,
    dump_model,
    error_code_of,
    map_outcome,
    summary_of,
)
from core.errors import StepFailed
from infrastructure.llm.runtime import close_sessions
from infrastructure.storage.lifecycle import LifecycleStore
from infrastructure.storage.redaction import public_exception
from infrastructure.storage.run_control import cancellation_requested, pause_requested
from infrastructure.storage.store import RecordStore
from workflow.engine.handoff import apply_step_result, outcome_of
from workflow.engine.routes import (
    APPROVE,
    CLAIM,
    FINALIZE,
    PHASE_COMPOSITIONS,
    PREPARE_CONTEXT,
    REPORT,
    RETRY,
    REVIEW,
    RUN_PHASES,
    SUBMIT,
    TASK_CONDITIONAL_TRANSITIONS,
    TASK_NODE_NAMES,
    TASK_STATIC_TRANSITIONS,
    UNIFIED_FIXER,
    dependencies_for,
    next_step_for,
)

if TYPE_CHECKING:
    from infrastructure.es.logs import ESLogAdapter
    from infrastructure.git.delivery import GitBranchDelivery
    from infrastructure.workspace.workspace import WorkspaceManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. 运行时服务契约 (Workflow Services)
# ---------------------------------------------------------------------------

@dataclass
class WorkflowServices:
    """一次执行的外部依赖集合。"""

    source_repo: Path | None = None
    project_subdir: Path | None = None
    git_push: bool = False
    git_remote: str = settings.default_git_remote
    command_timeout_seconds: int = 600
    workspaces: WorkspaceManager | None = None
    delivery: GitBranchDelivery | None = None
    log_source: ESLogAdapter | None = None
    ignore_errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def close(self) -> None:
        """外部资源由适配器自己持有，这里没有共享通道要关。"""


# ---------------------------------------------------------------------------
# 2. 插件注册表 (Plugin Registry)
# ---------------------------------------------------------------------------

class PluginError(RuntimeError):
    """插件契约不完整、名字冲突或未注册。"""


_REGISTRY: dict[str, PluginSpec] = {}


def register(spec: PluginSpec) -> PluginSpec:
    """登记一个插件。同名重复登记只在是同一个对象时放行，否则报错。"""
    existing = _REGISTRY.get(spec.name)
    if existing is not None and existing is not spec:
        raise PluginError(f"插件名冲突: {spec.name}")
    _REGISTRY[spec.name] = spec
    return spec


def resolve(name: str) -> PluginSpec:
    """按插件名取契约。取不到就报错，不做兜底。"""
    spec = _REGISTRY.get(name)
    if spec is None:
        raise PluginError(f"未注册的插件: {name}")
    return spec


def steps() -> tuple[StepSpec, ...]:
    """全部步骤插件，按注册名排序。"""
    found = (item for item in _REGISTRY.values() if isinstance(item, StepSpec))
    return tuple(sorted(found, key=lambda item: item.name))


def agents() -> tuple[AgentSpec, ...]:
    """全部 agent 插件，按注册名排序。"""
    found = (item for item in _REGISTRY.values() if isinstance(item, AgentSpec))
    return tuple(sorted(found, key=lambda item: item.name))


def names() -> tuple[str, ...]:
    """已注册的插件名。"""
    return tuple(sorted(_REGISTRY))


def clear() -> None:
    """清空注册表。只给测试和回放用。"""
    _REGISTRY.clear()


# ---------------------------------------------------------------------------
# 3. 插件自检契约 (Plugin Checks)
# ---------------------------------------------------------------------------

_AGENT_KINDS = {"unified_fixer", "review"}


def verify_registry() -> None:
    """校验全部已注册插件。任一条不满足直接抛 PluginError。"""
    problems: list[str] = []
    for spec in steps():
        problems.extend(_check_step(spec))
    for spec in agents():
        problems.extend(_check_agent(spec))
    problems.extend(_check_cycles())
    problems.extend(_check_task_topology())
    if problems:
        raise PluginError("插件契约自检失败:\n- " + "\n- ".join(problems))


def _known(name: str) -> bool:
    try:
        resolve(name)
    except PluginError:
        return False
    return True


def _check_task_topology() -> list[str]:
    from langgraph.graph import END, START

    problems: list[str] = []
    declared = set(TASK_NODE_NAMES)
    registered = {spec.name for spec in steps() if spec.task_level}
    registered |= {spec.name for spec in agents() if spec.task_level}
    composed_task_plugins = {
        member
        for phase, members in PHASE_COMPOSITIONS.items()
        if phase in declared
        for member in members
        if member in registered
    }
    for phase, members in PHASE_COMPOSITIONS.items():
        if phase not in declared and phase not in RUN_PHASES:
            problems.append(f"组合阶段未纳入工作流: {phase}")
        for member in members:
            if not _known(member):
                problems.append(f"组合阶段 {phase} 引用了未注册插件: {member}")
    registered -= composed_task_plugins
    registered.add("retry_prepare")
    for name in sorted(registered - declared):
        problems.append(f"任务插件未纳入任务流程: {name}")
    for name in sorted(declared - registered):
        if name not in PHASE_COMPOSITIONS:
            problems.append(f"任务流程声明了不存在的节点: {name}")

    edges: list[tuple[str, str]] = list(TASK_STATIC_TRANSITIONS)
    for source, _router, targets in TASK_CONDITIONAL_TRANSITIONS:
        edges.extend((source, target) for target in targets)
    for source, target in edges:
        if source not in {START, END} and source not in declared:
            problems.append(f"任务流程边的源节点未声明: {source}")
        if target not in {START, END} and target not in declared:
            problems.append(f"任务流程边的目标节点未声明: {target}")
    conditional_sources = [
        source for source, _router, _targets in TASK_CONDITIONAL_TRANSITIONS
    ]
    if len(conditional_sources) != len(set(conditional_sources)):
        problems.append("任务流程同一节点声明了多组条件路由")
    return problems


def _check_models(spec: StepSpec | AgentSpec) -> list[str]:
    problems: list[str] = []
    for label, model in (
        ("input_model", spec.input_model),
        ("output_model", spec.output_model),
    ):
        if not isinstance(model, type) or not issubclass(model, BaseModel):
            problems.append(f"{spec.name}.{label} 不是 pydantic 模型")
    return problems


def _check_depends_on(spec: StepSpec | AgentSpec) -> list[str]:
    return [
        f"{spec.name}.depends_on 指向未注册插件: {dependency.name}"
        for dependency in spec.depends_on
        if not _known(dependency.name)
    ]


def _check_step(spec: StepSpec) -> list[str]:
    if not callable(spec.runner):
        return [f"{spec.name}.runner 不可调用"]
    return _check_models(spec) + _check_depends_on(spec)


def _check_agent(spec: AgentSpec) -> list[str]:
    problems = _check_models(spec) + _check_depends_on(spec)
    if spec.kind not in _AGENT_KINDS:
        problems.append(f"{spec.name}.kind 没有对应子图实现: {spec.kind}")
    return problems


def _check_cycles() -> list[str]:
    graph_map: dict[str, list[str]] = {
        spec.name: [dependency.name for dependency in spec.depends_on]
        for spec in steps()
    }
    visiting: set[str] = set()
    visited: set[str] = set()
    problems: list[str] = []

    def walk(name: str, trail: tuple[str, ...]) -> None:
        if name in visiting:
            problems.append("步骤依赖成环: " + " -> ".join((*trail, name)))
            return
        if name in visited:
            return
        visiting.add(name)
        for parent in graph_map.get(name, []):
            if parent in graph_map:
                walk(parent, (*trail, name))
        visiting.discard(name)
        visited.add(name)

    for name in graph_map:
        walk(name, ())
    return problems


# ---------------------------------------------------------------------------
# 4. 动态装载内置插件 (Plugin Loader)
# ---------------------------------------------------------------------------

STEP_LOCATIONS: dict[str, str] = {
    "claim_repair_slot": "workflow.stages.claim_slot",
    "request_approval": "workflow.stages.request_approval",
    "prepare_evidence": "workflow.stages.prepare_context.prepare_evidence",
    "prepare_source_snapshot": "workflow.stages.prepare_context.prepare_source_snapshot",
    "prepare_workspace": "workflow.stages.prepare_context.prepare_workspace",
    "repair_branch_preflight": "workflow.stages.prepare_context.repair_branch_preflight",
    "generate_report": "workflow.stages.generate_report",
    "submit_changes": "workflow.stages.submit_changes",
    "finalize_execution": "workflow.stages.finalize",
    "discover_logs": "workflow.stages.log_pipeline.discover_logs",
    "normalize_error": "workflow.stages.log_pipeline.normalize_error",
    "deduplicate_error": "workflow.stages.log_pipeline.deduplicate_error",
    "identify_carrier": "workflow.stages.log_pipeline.identify_carrier",
}

BUILTIN_STEPS = tuple(STEP_LOCATIONS.keys())
BUILTIN_AGENTS = ("unified_fixer", "review")

_LOADED = False


def _spec_of(module: ModuleType) -> PluginSpec:
    spec = getattr(module, "SPEC", None)
    if not isinstance(spec, (StepSpec, AgentSpec)):
        raise PluginError(f"插件包缺少合法 SPEC: {module.__name__}")
    return spec


def load_step(name: str) -> PluginSpec:
    if name not in STEP_LOCATIONS:
        raise PluginError(f"未注册的内置步骤: {name}")
    pkg = STEP_LOCATIONS[name]
    return register(_spec_of(importlib.import_module(pkg)))


def load_agent(name: str) -> PluginSpec:
    short_name = name.replace("_agent", "") if name.endswith("_agent") else name
    if short_name not in BUILTIN_AGENTS:
        raise PluginError(f"未注册的内置 Agent: {name}")
    return register(_spec_of(importlib.import_module(f"agents.{short_name}")))


def load_builtin() -> None:
    for name in BUILTIN_STEPS:
        load_step(name)
    for name in BUILTIN_AGENTS:
        load_agent(name)


def load_all() -> None:
    global _LOADED
    if _LOADED:
        return
    load_builtin()
    _LOADED = True


def reset() -> None:
    global _LOADED
    _LOADED = False


# ---------------------------------------------------------------------------
# 5. 步骤执行与节点绑定引擎 (Stage Runner & Node Binding)
# ---------------------------------------------------------------------------

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
    from workflow.engine.routes import assert_workflow_version

    assert_workflow_version(store.load_run(run_id))
    task_id = str(state.get("task_id") or "") if spec.task_level else ""
    if spec.task_level and not task_id:
        raise StepFailed(f"{spec.name} 缺少真实 task_id，拒绝生成调试身份")

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
    artifacts: list[ArtifactRef] = []
    try:
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
            spec, store, state, record, task_id, artifacts, started, error, **_views(spec)
        )
    except (ValidationError, KeyError, ValueError) as error:
        return _save_fail(
            spec, store, state, record, task_id, artifacts, started, StepFailed(str(error)), **_views(spec)
        )
    except Exception as error:
        logger.exception("步骤 %s 执行异常", spec.name)
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
        return _save_fail(
            spec, store, state, record, task_id, artifacts, started, error, **_views(spec)
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
    if step_name in {
        "prepare_source_snapshot",
        "repair_branch_preflight",
        "prepare_workspace",
        "prepare_evidence",
    }:
        return PREPARE_CONTEXT
    if step_name == "generate_report":
        return REPORT
    if step_name == "submit_changes":
        return SUBMIT
    if step_name == "claim_repair_slot":
        return CLAIM
    return ""


def _failure_route_patch(result: StepResult) -> dict[str, Any]:
    if result.failure is None:
        return {}
    target = str(result.output.get("retry_target") or "")
    if target not in {"unified_fixer_agent", "review_agent"}:
        return {}
    reason = str(result.output.get("failure_reason") or result.failure.reason)
    return {"retry_target": target, "retry_reason": reason[:2000]}


def _views(spec: StepSpec) -> dict[str, Any]:
    return {"input_view": spec.input_record, "output_view": spec.output_record}


def bind(
    spec: StepSpec,
    store: RecordStore,
    services: WorkflowServices,
    *,
    next_step: str | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """单步调试与图节点绑定入口。"""

    def node(state: dict[str, Any]) -> dict[str, Any]:
        run_id = str(state.get("run_id") or "")
        if spec.name == "finalize_execution" and cancellation_requested(
            store.root, run_id
        ):
            state = {**state, "terminal_status": "CANCELLED"}
        result = run_stage(spec, store, services, state)
        cancelled = spec.name != "finalize_execution" and cancellation_requested(
            store.root, run_id
        )

        produced = spec.produced(result.output)
        rejected = (
            spec.name == "request_approval"
            and str(result.output.get("decision") or "").upper() == "REJECT"
        )
        if result.failure is None and produced and not rejected:
            patch = spec.patch(result.output)
            route_status = str(patch.get("last_result") or result.status)
            target = str(patch.get("retry_target") or "") or (
                _retry_target_for_step(spec.name) if route_status == "failed" else ""
            )
            patch["retry_target"] = target
            if target:
                patch["retry_reason"] = (
                    summary_of(result.output) or f"{spec.name} 返回失败结论"
                )
        elif rejected:
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
            patch = _failure_route_patch(result)
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
            if spec.name in {REPORT, SUBMIT}:
                key = "delivery_candidate" if spec.name == REPORT else "delivery"
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
    record.status = status
    record.result = business
    record.summary = summary_of(output) or public_exception(
        error, prefix=f"步骤 {spec.name}"
    )
    record.artifacts = list(artifacts)
    record.output = output
    retryable = error.result == "failed"
    if status == "failed":
        record.error = StepError(
            code=error_code_of(error.code),
            message=public_exception(error, prefix=f"步骤 {spec.name} 执行失败"),
            retryable=retryable,
        )
    record.finished_at = utc_now()
    record.duration_ms = round((time.perf_counter() - started) * 1000, 2)
    formal = outcome_of(
        record.output,
        execution_status="failed" if record.status == "failed" else "succeeded",
        error={
            "code": str(record.error.code),
            "message": record.error.message,
            "retryable": retryable,
        }
        if record.error
        else None,
        summary=record.summary,
        business=record.result,
    )
    record.execution, record.assessment = formal.execution, formal.assessment
    try:
        store.save(str(state.get("run_id") or ""), record, task_id, **views)
    except StepFailed:
        minimal = record.model_copy(deep=True)
        minimal.input = {}
        minimal.output = {}
        try:
            store.save(str(state.get("run_id") or ""), minimal, task_id)
        except Exception:
            logger.exception("步骤 %s 的最小失败记录也无法落盘", spec.name)
    return StepResult(
        outcome=outcome_of(
            record.output,
            execution_status="failed" if status == "failed" else "succeeded",
            error={
                "code": str(record.error.code) if record.error else "STEP_EXECUTION_FAILED",
                "message": record.summary,
                "retryable": retryable,
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


# ---------------------------------------------------------------------------
# 6. 单步执行应用服务 (StepRunner Application Service)
# ---------------------------------------------------------------------------

ACTIVE_RUN_STATUSES = frozenset(
    {"queued", "running", "pause_requested", "cancel_requested"}
)
BLOCKED_STEPS = frozenset({"request_approval", "retry_prepare"})


class StepRunnerError(RuntimeError):
    def __init__(
        self, message: str, *, code: str = "STEP_RUN_REJECTED", status_code: int = 409
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class StepRunRequest:
    run_id: str
    task_id: str
    step_name: str
    confirm_write: bool = False
    continue_after: bool = False


def default_store() -> RecordStore:
    from infrastructure.llm.config import load_config
    return RecordStore(load_config().runtime_dir)


def resolve_spec(name: str) -> PluginSpec:
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
    missing: list[str] = []
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
    from core.contracts import Handoff
    from infrastructure.llm.config import load_config
    from workflow.engine.routes import assert_workflow_version

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
    if not task_id:
        return
    previous = store.load(run_id, spec.name, task_id)
    if previous is None or not (
        previous.status == "failed"
        or str(previous.result or "").upper() in {"FAIL", "FAILED", "REJECTED"}
    ):
        return
    from workflow.engine.handoff import capture_retry_context

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
    """同步执行一个步骤或 Agent，返回 (退出码, 步骤记录, 节点结果)。"""
    _require_identity(spec, run_id, task_id)
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
        from workflow.engine.graph import build_agent_node
        node = build_agent_node(spec, store, services=services)
    else:
        from infrastructure.git.source import sync_source_repo
        from infrastructure.llm.config import load_config
        from workflow.engine.graph import build_services

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
        self._on_finish = on_finish
        self._on_continue = on_continue

    def validate(self, request: StepRunRequest) -> PluginSpec:
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
        LifecycleStore(self._store.root).update_run(
            request.run_id, allow_reactivate=True, status="running",
            phase="single_step", current_step=spec.name, error={},
        )
        LifecycleStore(self._store.root).update_task(
            request.run_id, request.task_id,
            allow_reactivate=True, status="running", current_step=spec.name,
            terminal_step="", terminal_reason="", error={},
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
                and record.status in {"succeeded", "waiting"}
            ):
                continuing = True
                self._on_continue(request, payload)
                return
            if code == 0:
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
            if not continuing:
                lifecycle = LifecycleStore(self._store.root)
                task = lifecycle.read_task(request.run_id, request.task_id)
                failed = task.get("status") == "failed"
                lifecycle.update_run(
                    request.run_id, status="failed" if failed else "paused",
                    phase="single_step", current_step=spec.name, worker_active=False,
                    error=task.get("error", {}) if failed else {},
                )
            if self._on_finish is not None:
                self._on_finish()

    def _continuation_failed(self, request: StepRunRequest, message: str) -> None:
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
    "BUILTIN_AGENTS",
    "BUILTIN_STEPS",
    "PluginError",
    "STEP_LOCATIONS",
    "StepFailed",
    "StepRunRequest",
    "StepRunner",
    "StepRunnerError",
    "WorkflowServices",
    "agents",
    "bind",
    "clear",
    "default_store",
    "dependencies_missing",
    "execute_once",
    "load_all",
    "load_agent",
    "load_builtin",
    "load_step",
    "names",
    "register",
    "reset",
    "resolve",
    "resolve_spec",
    "run_stage",
    "state_from_records",
    "steps",
    "verify_registry",
]
