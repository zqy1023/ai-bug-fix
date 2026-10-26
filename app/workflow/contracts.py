"""工作流 v7 的正式交接协议；展示摘要不参与状态恢复。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

WORKFLOW_VERSION = 7


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


class AcceptanceAssertion(ContractModel):
    assertion_id: str
    description: str
    required: bool = True
    method: Literal["baseline_replay", "fixed_replay", "live_request", "code_review"]
    verifier: str
    expected: dict[str, Any]


class AcceptanceContract(ContractModel):
    contract_id: str
    problem_id: str
    baseline_id: str
    diagnosis_id: str
    assertions: list[AcceptanceAssertion]

    @model_validator(mode="after")
    def unique_assertions(self) -> AcceptanceContract:
        ids = [item.assertion_id for item in self.assertions]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("验收断言必须非空且标识唯一")
        return self


class AssertionResult(ContractModel):
    assertion_id: str
    status: Literal["passed", "failed", "unverified"]
    reason_code: str
    summary: str
    report_path: str = ""
    actual: dict[str, Any] = Field(default_factory=dict)
    failure_origin: (
        Literal["code", "environment", "input", "verifier", "unknown"] | None
    ) = None


class ValidationReport(ContractModel):
    binding: EvidenceBinding
    results: list[AssertionResult]


class TaskIdentity(ContractModel):
    run_id: str
    task_id: str = ""
    task: dict[str, Any] = Field(default_factory=dict)
    problem: dict[str, Any] = Field(default_factory=dict)


class TaskPolicy(ContractModel):
    workflow_version: Literal[7] = WORKFLOW_VERSION
    max_attempts: int = 3
    review_mode: Literal["agent", "human"] = "agent"
    allow_push: bool = False
    require_push: bool = False


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

    locate: dict[str, Any] = Field(default_factory=dict)
    acceptance_contract: dict[str, Any] = Field(default_factory=dict)
    baseline_reproduction: dict[str, Any] = Field(default_factory=dict)
    repair: dict[str, Any] = Field(default_factory=dict)
    change_plan: dict[str, Any] = Field(default_factory=dict)
    gate_result: dict[str, Any] = Field(default_factory=dict)
    syntax_result: dict[str, Any] = Field(default_factory=dict)
    validation_plan: dict[str, Any] = Field(default_factory=dict)
    verify_result: dict[str, Any] = Field(default_factory=dict)
    regression_result: dict[str, Any] = Field(default_factory=dict)
    validation_summary: dict[str, Any] = Field(default_factory=dict)
    validation_decision: dict[str, Any] = Field(default_factory=dict)
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

    workflow_version: Literal[7] = WORKFLOW_VERSION
    step: str
    outcome: StepOutcome
    state: TaskSnapshot


class BaselineReproduction(ContractModel):
    retryable: bool = False
    problem_id: str
    baseline_id: str
    status: Literal["passed", "failed", "unverified"]
    reason: str
    report_path: str


class TaskResolution(ContractModel):
    result: Literal[
        "delivered",
        "analysis_delivered",
        "skipped",
        "blocked",
        "failed",
        "rejected",
        "cancelled",
    ]
    reason: str
    terminal_step: str
    next_action: str
