"""Agent Prompt 的强类型输入模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.application.contracts import AgentRole


class PromptContext(BaseModel):
    """渲染 Prompt 所需的真实任务状态与确定性证据。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_role: AgentRole
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    service_name: str = Field(min_length=1)
    biztype: str = Field(min_length=1)
    carrier: str = Field(min_length=1)
    framework: Literal["gathers", "spiders"]
    problem: dict[str, Any]
    source_snapshot: dict[str, Any]
    evidence: dict[str, Any]
    changed_files: tuple[str, ...]
    diff_summary: str
    gate_results: dict[str, Any]
    verification_results: dict[str, Any]
    regression_results: dict[str, Any]
    allowed_scope: tuple[str, ...]
    stop_conditions: tuple[str, ...]
    allowed_read_roots: tuple[str, ...]
    allowed_write_roots: tuple[str, ...]
    budget: dict[str, int]
    retry_feedback: str = ""


class PromptDocument(BaseModel):
    """单份 Markdown Prompt 的版本和正文。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1)
    body: str = Field(min_length=1)


class RenderedPrompt(BaseModel):
    """一次 Prompt 渲染结果及可审计标识。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    system_prompt: str
    prompt: str
    prompt_version: str
    prompt_hash: str


__all__ = ["PromptContext", "PromptDocument", "RenderedPrompt"]
