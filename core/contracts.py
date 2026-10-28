"""核心统一数据契约、快照模型、审计与上下文定义（系统唯一事实源）。"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, TypeVar, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator



from collections.abc import Callable, Mapping
from dataclasses import dataclass


if TYPE_CHECKING:
    from core.contracts import StepContext

Level = Literal["run", "task"]

Runner = Callable[["StepContext", BaseModel], BaseModel]
InputBuilder = Callable[[Mapping[str, Any]], dict[str, Any]]
OutputPatch = Callable[[dict[str, Any]], dict[str, Any]]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class RecordView:
    keep: tuple[str, ...] = ()
    trim: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def is_empty(self) -> bool:
        return not self.keep and not self.trim


@dataclass(frozen=True)
class Dependency:
    name: str
    level: Level = "run"


@dataclass(frozen=True)
class StepSpec:
    name: str
    title: str
    runner: Runner
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    input_builder: InputBuilder
    task_level: bool = True
    requires_confirmation: bool = False
    output_patch: OutputPatch | None = None
    input_record: RecordView | None = None
    output_record: RecordView | None = None

    @property
    def depends_on(self) -> tuple[Dependency, ...]:
        from workflow.engine.definitions import dependencies_for
        return dependencies_for(self.name)

    def patch(self, output: dict[str, Any]) -> dict[str, Any]:
        return self.output_patch(output) if self.output_patch else {}

    def produced(self, output: dict[str, Any]) -> bool:
        try:
            self.output_model.model_validate(dict(output))
        except ValidationError:
            return False
        return True


@dataclass(frozen=True)
class AgentSpec:
    name: str
    title: str
    kind: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    task_level: bool = True
    requires_confirmation: bool = False
    input_record: RecordView | None = None
    output_record: RecordView | None = None

    @property
    def depends_on(self) -> tuple[Dependency, ...]:
        from workflow.engine.definitions import dependencies_for
        return dependencies_for(self.name)


PluginSpec = StepSpec | AgentSpec





ScopeTier = Literal["carrier", "project", "manual"]
EditAction = Literal["create", "update", "delete"]


class FileEdit(FrozenModel):
    """单个文件的确定改动。content 是完整新内容，路径为工作区相对路径。"""
    path: str
    action: EditAction = "update"
    content: str = ""
    note: str = ""
    symbol: str = ""
    problem: str = ""
    operation: str = ""
    rationale: str = ""
    validation_notes: str = ""
    risk: str = ""


class ChangePlan(FrozenModel):
    """一次代码修复的结构化方案。"""
    scope: ScopeTier = "carrier"
    edits: tuple[FileEdit, ...] = ()
    rationale: str = ""

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(edit.path for edit in self.edits)


def plan_of(payload: dict[str, Any]) -> ChangePlan | None:
    raw = payload.get("change_plan")
    if not isinstance(raw, dict):
        return None
    try:
        plan = ChangePlan.model_validate(raw)
    except Exception:
        return None
    return plan if plan.edits or plan.rationale or plan.scope != "carrier" else None






WORKFLOW_VERSION = 7

AgentRole = Literal["unified_fixer", "review"]
StepStatus = Literal["passed", "failed", "skipped", "manual"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExecutionError(ContractModel):
    code: str
    message: str
    retryable: bool = False


class ExecutionResult(ContractModel):
    status: Literal["succeeded", "failed", "waiting", "cancelled"]
    error: ExecutionError | None = None

    @model_validator(mode="after")
    def validate_error(self) -> ExecutionResult:
        if (self.status == "failed") != (self.error is not None):
            raise ValueError("只有执行失败必须携带 error")
        return self


class Assessment(ContractModel):
    outcome: Literal["passed", "failed", "inconclusive", "not_applicable"]
    code: str
    summary: str
    findings: list[str] = Field(default_factory=list)


class StepOutcome(ContractModel):
    execution: ExecutionResult
    assessment: Assessment
    output: dict[str, Any]
    artifact_paths: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_assessment(self) -> StepOutcome:
        if self.execution.status != "succeeded" and self.assessment.outcome == "passed":
            raise ValueError("未成功执行的步骤不能声明业务通过")
        return self


class EvidenceBinding(ContractModel):
    problem_id: str
    baseline_id: str
    acceptance_id: str
    revision_id: str
    policy_id: str










class TaskIdentity(ContractModel):
    run_id: str
    task_id: str = ""
    task: dict[str, Any] = Field(default_factory=dict)
    problem: dict[str, Any] = Field(default_factory=dict)


class TaskPolicy(ContractModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    workflow_version: int = WORKFLOW_VERSION
    max_attempts: int = 3
    review_mode: Literal["agent", "human"] = "agent"
    allow_push: bool = False
    require_push: bool = False
    agent_policy: dict[str, Any] = Field(default_factory=dict)
    policy_sources: dict[str, Any] = Field(default_factory=dict)


class TaskControl(ContractModel):
    attempt: int = 1
    automatic_attempt: int = 1
    validation_recovery_counts: dict[str, int] = Field(default_factory=dict)
    retry_target: str = ""
    retry_reason: str = ""
    retry_context: dict[str, Any] = Field(default_factory=dict)
    continuation: dict[str, Any] = Field(default_factory=dict)
    terminal_status: str = ""
    last_result: str = "passed"
    decision: str = "PENDING"
    approval_granted: bool = False
    approval_cleanup_failed: bool = False
    approval_recovery_blocked: bool = False
    repair_slot_claimed: bool = False
    scope_escalation_required: bool = False
    blocked_reason: str = ""
    next_step: str | None = None
    manual_confirmation_required: bool = False
    manual_confirmation_reasons: list[str] = Field(default_factory=list)
    review_contract_error: str = ""


class SourceContext(ContractModel):
    source_repo_path: str = ""
    source_project_path: str = ""
    ai_workspace_path: str = ""
    baseline_dir: str = ""
    baseline_manifest: dict[str, str] = Field(default_factory=dict)
    framework: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    changed_files: list[str] = Field(default_factory=list)
    repair_changed_files: list[str] = Field(default_factory=list)


class TaskArtifacts(ContractModel):
    """每个正式产物一个槽位；具体内容在步骤边界按其输出模型校验。"""
    # 历史快照可能包含已经停用的产物；读取时忽略，不重新引入执行状态。
    model_config = ConfigDict(extra="ignore", frozen=True)
    unified_fixer: dict[str, Any] = Field(default_factory=dict)
    locate: dict[str, Any] = Field(default_factory=dict)
    repair: dict[str, Any] = Field(default_factory=dict)
    change_plan: dict[str, Any] = Field(default_factory=dict)
    review: dict[str, Any] = Field(default_factory=dict)
    delivery_candidate: dict[str, Any] = Field(default_factory=dict)
    approval_result: dict[str, Any] = Field(default_factory=dict)
    delivery: dict[str, Any] = Field(default_factory=dict)
    analysis_report: dict[str, Any] = Field(default_factory=dict)
    task_summary: dict[str, Any] = Field(default_factory=dict)
    resolution: dict[str, Any] | None = None
    repair_branch: str = ""
    delivery_commit: str = ""
    delivery_pushed: bool = False
    delivery_local_branch: bool = False
    analysis_checks: dict[str, Any] = Field(default_factory=dict)
    analysis_only: bool = False


class RunChannels(ContractModel):
    schema_version: int = 2
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    task_cursor: int = 0
    task_results: list[dict[str, Any]] = Field(default_factory=list)
    log_service_name: str = ""
    log_service_prefix: str = ""
    log_excluded_messages: list[str] = Field(default_factory=list)
    log_excluded_biztypes: list[str] = Field(default_factory=list)
    log_hours: int = 1
    log_limit: int = 0


class TaskSnapshot(ContractModel):
    """正式持久化状态；扁平通道只存在于 LangGraph 执行适配层。"""
    identity: TaskIdentity
    policy: TaskPolicy
    control: TaskControl
    context: SourceContext
    artifacts: TaskArtifacts
    run: RunChannels
    outcomes: dict[str, StepOutcome] = Field(default_factory=dict)

    @classmethod
    def from_graph(cls, state: dict[str, Any]) -> TaskSnapshot:
        groups = {
            "identity": TaskIdentity,
            "policy": TaskPolicy,
            "control": TaskControl,
            "context": SourceContext,
            "artifacts": TaskArtifacts,
            "run": RunChannels,
        }
        values = {
            name: model.model_validate(
                {key: state[key] for key in model.model_fields if key in state}
            )
            for name, model in groups.items()
        }
        return cls(**values, outcomes=state.get("step_outcomes") or {})

    def to_graph(self) -> dict[str, Any]:
        state: dict[str, Any] = {}
        for name in ("identity", "policy", "control", "context", "artifacts", "run"):
            state.update(getattr(self, name).model_dump(mode="json"))
        state["step_outcomes"] = {
            key: item.model_dump(mode="json") for key, item in self.outcomes.items()
        }
        return state


class Handoff(ContractModel):
    """完整步骤结果及其应用后的状态快照，用于无损单步续跑。"""
    model_config = ConfigDict(extra="ignore", frozen=True)
    workflow_version: int = WORKFLOW_VERSION
    step: str
    outcome: StepOutcome
    state: TaskSnapshot




class TaskResolution(ContractModel):
    result: Literal[
        "delivered",
        "analysis_delivered",
        "waiting_approval",
        "skipped",
        "blocked",
        "failed",
        "rejected",
        "cancelled",
    ]
    reason: str
    terminal_step: str
    next_action: str


class AgentRequest(BaseModel):
    """携带渲染提示词和权限范围的一次 Agent 调用。"""
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_role: AgentRole
    run_id: str
    task_id: str
    attempt: int = Field(ge=1)
    prompt_context: dict[str, Any]
    input_evidence: dict[str, Any]
    budget: dict[str, int]
    stage: str
    workspace: str
    system_prompt: str
    prompt: str
    prompt_version: str
    prompt_hash: str
    model: str
    allowed_read_roots: tuple[str, ...]
    allowed_write_roots: tuple[str, ...]
    write_root: str | None = None
    provider: str = ""
    logical_context_hash: str = ""
    context_mode: Literal["full", "delta"] = "full"
    previous_context_hash: str = ""


class StepFailure(ContractModel):
    """Structured step failure details."""

    code: str
    reason: str
    recoverable: bool


class StepAudit(ContractModel):
    """Compact input/output and timing summary for one step."""

    input_summary: dict[str, Any]
    output_summary: dict[str, Any]
    elapsed_ms: float = Field(ge=0)


class StepResult(ContractModel):
    """正式执行结果；提供与历史插件/调用兼容的视图属性。"""

    outcome: StepOutcome
    audit: StepAudit

    @property
    def output(self) -> dict[str, Any]:
        return self.outcome.output

    @property
    def status(self) -> str:
        value = self.outcome.assessment.outcome
        if value == "not_applicable":
            return "skipped"
        if value == "inconclusive":
            return "failed" if self.failure else "manual"
        return "passed" if value == "passed" else "failed"

    @property
    def failure(self) -> StepFailure | None:
        error = self.outcome.execution.error
        return (
            StepFailure(
                code=error.code, reason=error.message, recoverable=error.retryable
            )
            if error
            else None
        )








StepPhase = Literal["pending", "running", "succeeded", "failed", "waiting", "skipped"]

StepResultValue = Literal[
    "UNVERIFIED",
    "BLOCKED",
    "LOCATED",
    "INSUFFICIENT_EVIDENCE",
    "MANUAL",
    "PASS",
    "FAIL",
    "REJECTED",
    "SKIPPED",
    "DELIVERED",
    "SUCCESS",
    "FAILED",
]

StepErrorCode = Literal[
    "STEP_EXECUTION_FAILED",
    "ARTIFACT_WRITE_FAILED",
    "ARTIFACT_NOT_FOUND",
    "ARTIFACT_PATH_INVALID",
    "ARTIFACT_INVALID_FORMAT",
    "OUTPUT_REHYDRATION_FAILED",
    "REPORT_WRITE_FAILED",
    "ACCEPTANCE_CONTEXT_MISSING",
    "APPROVAL_CONTEXT_MISSING",
    "CANDIDATE_EVIDENCE_STALE",
    "REPRODUCE_CONTEXT_MISSING",
    "DELIVERY_EXECUTION_ERROR",
    "LOG_QUERY_ARGS_INVALID",
    "PROXY_MANAGER_ADDRESS_MISSING",
    "RUN_CANCEL_REQUESTED",
    "STEP_RECORD_CORRUPTED",
    "SYNTAX_INVALID",
    "AGENT_EXECUTION_FAILED",
    "AGENT_INPUT_INVALID",
    "BUSINESS_STEP_FAILED",
    "CONFIRM_WRITE_REQUIRED",
    "INTERNAL_REVIEW_STEP",
    "RUN_ACTIVE",
    "RUN_ID_REQUIRED",
    "RUN_NOT_FOUND",
    "STEP_DEPENDENCY_MISSING",
    "STEP_NOT_ALLOWED",
    "STEP_NOT_FOUND",
    "STEP_NOT_TASK_LEVEL",
    "TASK_AWAITING_APPROVAL",
    "TASK_ID_REQUIRED",
]

MAX_TEXT_CHARS = 600
MAX_DEPTH = 4
MAX_DEPTH_KEYS = 8
MAX_LIST_ITEMS = 8

RUN_LEVEL_FIELDS = frozenset({"ai_workspace_path"})
_RUN_LEVEL_KEEP = frozenset({"prepare_workspace"})
SCHEMA_VERSION = 1


def project(value: Any, view: RecordView | None, step_name: str = "") -> Any:
    result = value
    if view is not None and not view.is_empty() and isinstance(result, dict):
        if view.keep:
            result = {key: item for key, item in result.items() if key in view.keep}
        for dotted, keys in view.trim:
            result = _trim(result, dotted.split("."), keys)
    if step_name not in _RUN_LEVEL_KEEP and isinstance(result, dict):
        result = {
            key: item for key, item in result.items() if key not in RUN_LEVEL_FIELDS
        }
    return brief_value(result)


def _trim(value: Any, parts: list[str], keys: tuple[str, ...]) -> Any:
    if not parts or not isinstance(value, (dict, list)):
        return value
    head, rest = parts[0], parts[1:]
    if isinstance(value, list):
        return [_trim(item, parts, keys) for item in value]
    if head not in value:
        return value
    if rest:
        return {**value, head: _trim(value[head], rest, keys)}
    inner = value[head]
    if isinstance(inner, list):
        return {
            **value,
            head: [
                {key: item[key] for key in keys if key in item}
                if isinstance(item, dict)
                else item
                for item in inner
            ],
        }
    if isinstance(inner, dict):
        return {**value, head: {key: inner[key] for key in keys if key in inner}}
    return value


def brief_value(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        if len(value) <= MAX_TEXT_CHARS:
            return value
        return f"{value[:MAX_TEXT_CHARS]}…(已截断，原长 {len(value)} 字符)"
    if isinstance(value, dict):
        if depth >= MAX_DEPTH and len(value) > MAX_DEPTH_KEYS:
            return {
                "_summary": f"{len(value)} 个字段的深层内容",
                "_keys": sorted(str(key) for key in value)[:MAX_LIST_ITEMS],
            }
        return {str(key): brief_value(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [brief_value(item, depth + 1) for item in value]
    return value


def brief_record(
    record: StepRecord,
    input_view: RecordView | None = None,
    output_view: RecordView | None = None,
) -> dict[str, Any]:
    return {
        **record.model_dump(mode="json"),
        "input": project(record.input, input_view, record.name),
        "output": project(record.output, output_view, record.name),
    }


SUMMARY_KEYS = ("summary", "reason", "verdict", "status", "delivery_status")


def summary_of(output: dict[str, Any]) -> str:
    for key in SUMMARY_KEYS:
        value = output.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def map_outcome(
    *,
    kind: Literal["ok", "failed", "interrupt"],
    step_result: str | None = None,
    output: dict[str, Any] | None = None,
    output_model: type[BaseModel] | None = None,
    business: str | None = None,
) -> tuple[str, str | None]:
    if kind == "interrupt":
        return "waiting", None
    if kind == "ok":
        declared = _business_result(output)
        if declared in {"MANUAL", "INSUFFICIENT_EVIDENCE", "UNVERIFIED"}:
            return "succeeded", declared
        if declared in {"REJECTED", "SKIPPED"}:
            return "succeeded", declared
        if _declared_business_failure(output, output_model):
            return "succeeded", "FAIL"
        return "succeeded", business or declared
    if step_result == "skipped":
        return "skipped", "SKIPPED"
    if step_result == "manual":
        return "succeeded", "MANUAL"
    if step_result == "rejected":
        return "succeeded", "REJECTED"
    if _declared_business_failure(output, output_model):
        return "succeeded", "FAIL"
    return "failed", None


def error_code_of(code: str) -> StepErrorCode:
    return code if code in get_args(StepErrorCode) else "STEP_EXECUTION_FAILED"


def _business_result(output: dict[str, Any] | None) -> str | None:
    if not isinstance(output, dict):
        return None
    if output.get("status") in {"syntax_only", "not_configured", "not_executed"}:
        return "UNVERIFIED"
    allowed = {
        "UNVERIFIED",
        "LOCATED",
        "INSUFFICIENT_EVIDENCE",
        "MANUAL",
        "PASS",
        "FAIL",
        "REJECTED",
        "SKIPPED",
        "DELIVERED",
        "SUCCESS",
        "FAILED",
    }
    for key in ("verdict", "delivery_status", "status"):
        value = output.get(key)
        if isinstance(value, str) and value.upper() in allowed:
            return value.upper()
    return None


def _declared_business_failure(
    output: dict[str, Any] | None,
    output_model: type[BaseModel] | None,
) -> bool:
    if output_model is None or output is None:
        return False
    if "passed" not in output_model.model_fields:
        return False
    return output.get("passed") is False


class ArtifactRef(FrozenModel):
    name: str
    path: str
    task_id: str = ""


class StepError(FrozenModel):
    code: StepErrorCode
    message: str
    retryable: bool = False


class StepRecord(FrozenModel):
    execution: ExecutionResult | None = None
    assessment: Assessment | None = None
    name: str
    title: str
    status: StepPhase = "pending"
    result: StepResultValue | None = None
    attempt: int = 0
    started_at: str = ""
    finished_at: str = ""
    duration_ms: float = 0.0
    summary: str = ""
    error: StepError | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    @model_validator(mode="after")
    def _error_matches_status(self) -> StepRecord:
        if self.status != "failed" and self.error is not None:
            raise ValueError(f"状态 {self.status} 的步骤记录不能携带 error")
        return self

    def public(
        self,
        input_view: RecordView | None = None,
        output_view: RecordView | None = None,
    ) -> dict[str, Any]:
        return brief_record(self, input_view, output_view)


def dump_model(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")





from dataclasses import dataclass, field


if TYPE_CHECKING:
    from core.contracts import ArtifactRef


@dataclass(frozen=True)
class RunContext:
    """一次工作流执行共享的身份与审计信息。"""
    run_id: str
    started_at: str
    source_environment: str | None = None
    config_version: str | None = None
    operator: str | None = None
    audit_id: str | None = None


@dataclass(frozen=True)
class TaskContext:
    """一个异常任务的身份与元数据。"""
    task_id: str
    service_name: str
    biztype: str
    carrier: str
    error_fingerprint: str
    source_repo: str = ""
    framework: str | None = None
    workspace_path: str | None = None
    baseline_manifest: dict[str, str] | None = None
    evidence_refs: tuple[str, ...] = ()
    attempt: int = 1


@dataclass(frozen=True)
class StepContext:
    """单个步骤执行注入的上下文。"""
    run: RunContext
    task: TaskContext | None
    store: Any
    services: Any
    artifacts: list[ArtifactRef] = field(default_factory=list)

    def _owner(self) -> str:
        return self.task.task_id if self.task is not None else ""

    def emit_artifact(self, name: str, relative: str, value: Any) -> ArtifactRef:
        from core.contracts import ArtifactRef

        owner = self._owner()
        try:
            path = self.store.write_artifact(self.run.run_id, relative, value, owner)
        except StepFailed:
            raise
        except OSError as error:
            raise StepFailed(
                f"步骤 {name} 写入产物 {relative} 失败: {error}",
                code="ARTIFACT_WRITE_FAILED",
            ) from error
        ref = ArtifactRef(name=name, path=path, task_id=owner)
        self.artifacts.append(ref)
        return ref

    def load_artifact(self, ref: ArtifactRef) -> Any:
        return self.store.read_ref(self.run.run_id, ref, self._owner())


