"""Agent 调用的统一应用层契约。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AgentRole = Literal["locate", "repair", "review"]


class AgentRequest(BaseModel):
    """携带渲染提示词和权限范围的一次 Agent 调用。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # agent_role：该模型字段用于承载对应契约中的结构化数据。
    agent_role: AgentRole
    # run_id：该模型字段用于承载对应契约中的结构化数据。
    run_id: str
    # task_id：该模型字段用于承载对应契约中的结构化数据。
    task_id: str
    # attempt：该模型字段用于承载对应契约中的结构化数据。
    attempt: int = Field(ge=1)
    # prompt_context：该模型字段用于承载对应契约中的结构化数据。
    prompt_context: dict[str, Any]
    # input_evidence：该模型字段用于承载对应契约中的结构化数据。
    input_evidence: dict[str, Any]
    # budget：该模型字段用于承载对应契约中的结构化数据。
    budget: dict[str, int]
    # stage：该模型字段用于承载对应契约中的结构化数据。
    stage: str
    # workspace：该模型字段用于承载对应契约中的结构化数据。
    workspace: str
    # system_prompt：该模型字段用于承载对应契约中的结构化数据。
    system_prompt: str
    # prompt：该模型字段用于承载对应契约中的结构化数据。
    prompt: str
    # prompt_version：该模型字段用于承载对应契约中的结构化数据。
    prompt_version: str
    # prompt_hash：该模型字段用于承载对应契约中的结构化数据。
    prompt_hash: str
    # model：该模型字段用于承载对应契约中的结构化数据。
    model: str
    # allowed_read_roots：该模型字段用于承载对应契约中的结构化数据。
    allowed_read_roots: tuple[str, ...]
    # allowed_write_roots：该模型字段用于承载对应契约中的结构化数据。
    allowed_write_roots: tuple[str, ...]
    # write_root：该模型字段用于承载对应契约中的结构化数据。
    write_root: str | None = None
    provider: str = ""
    logical_context_hash: str = ""
    context_mode: Literal["full", "delta"] = "full"
    previous_context_hash: str = ""


StepStatus = Literal["passed", "failed", "skipped", "manual"]


class StepFailure(BaseModel):
    """Structured step failure details."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # code：该模型字段用于承载对应契约中的结构化数据。
    code: str
    # reason：该模型字段用于承载对应契约中的结构化数据。
    reason: str
    # recoverable：该模型字段用于承载对应契约中的结构化数据。
    recoverable: bool


class StepAudit(BaseModel):
    """Compact input/output and timing summary for one step."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # input_summary：该模型字段用于承载对应契约中的结构化数据。
    input_summary: dict[str, Any]
    # output_summary：该模型字段用于承载对应契约中的结构化数据。
    output_summary: dict[str, Any]
    # elapsed_ms：该模型字段用于承载对应契约中的结构化数据。
    elapsed_ms: float = Field(ge=0)


class StepResult(BaseModel):
    """Uniform outcome returned by the step execution boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # status：该模型字段用于承载对应契约中的结构化数据。
    status: StepStatus
    # output：该模型字段用于承载对应契约中的结构化数据。
    output: dict[str, Any]
    # failure：该模型字段用于承载对应契约中的结构化数据。
    failure: StepFailure | None = None
    # next_action：该模型字段用于承载对应契约中的结构化数据。
    next_action: str | None = None
    # audit：该模型字段用于承载对应契约中的结构化数据。
    audit: StepAudit

__all__ = [
    "AgentRequest",
    "AgentRole",
    "StepAudit",
    "StepFailure",
    "StepResult",
    "StepStatus",
]
