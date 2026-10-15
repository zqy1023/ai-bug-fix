"""Agent 调用的统一应用层契约。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AgentRole = Literal["locate", "repair", "review"]


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


StepStatus = Literal["passed", "failed", "skipped", "manual"]


class StepFailure(BaseModel):
    """Structured step failure details."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    reason: str
    recoverable: bool


class StepAudit(BaseModel):
    """Compact input/output and timing summary for one step."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_summary: dict[str, Any]
    output_summary: dict[str, Any]
    elapsed_ms: float = Field(ge=0)


class StepResult(BaseModel):
    """Uniform outcome returned by the step execution boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: StepStatus
    output: dict[str, Any]
    failure: StepFailure | None = None
    next_action: str | None = None
    audit: StepAudit

__all__ = [
    "AgentRequest",
    "AgentRole",
    "StepAudit",
    "StepFailure",
    "StepResult",
    "StepStatus",
]
