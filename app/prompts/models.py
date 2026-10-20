"""Agent Prompt 的强类型输入模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.application.contracts import AgentRole


class PromptContext(BaseModel):
    """渲染 Prompt 所需的真实任务状态与确定性证据。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # agent_role：字段说明见该模型文档与工作流契约。
    agent_role: AgentRole
    # run_id：字段说明见该模型文档与工作流契约。
    run_id: str = Field(min_length=1)
    # task_id：字段说明见该模型文档与工作流契约。
    task_id: str = Field(min_length=1)
    # attempt：字段说明见该模型文档与工作流契约。
    attempt: int = Field(ge=1)
    # service_name：字段说明见该模型文档与工作流契约。
    service_name: str = Field(min_length=1)
    # biztype：字段说明见该模型文档与工作流契约。
    biztype: str = Field(min_length=1)
    # carrier：字段说明见该模型文档与工作流契约。
    carrier: str = Field(min_length=1)
    # framework：字段说明见该模型文档与工作流契约。
    framework: Literal["gathers", "spiders"]
    # problem：字段说明见该模型文档与工作流契约。
    problem: dict[str, Any]
    # source_snapshot：字段说明见该模型文档与工作流契约。
    source_snapshot: dict[str, Any]
    # evidence：字段说明见该模型文档与工作流契约。
    evidence: dict[str, Any]
    # changed_files：字段说明见该模型文档与工作流契约。
    changed_files: tuple[str, ...]
    # diff_summary：字段说明见该模型文档与工作流契约。
    diff_summary: str
    # gate_results：字段说明见该模型文档与工作流契约。
    gate_results: dict[str, Any]
    # verification_results：字段说明见该模型文档与工作流契约。
    verification_results: dict[str, Any]
    # regression_results：字段说明见该模型文档与工作流契约。
    regression_results: dict[str, Any]
    # allowed_scope：字段说明见该模型文档与工作流契约。
    allowed_scope: tuple[str, ...]
    # stop_conditions：字段说明见该模型文档与工作流契约。
    stop_conditions: tuple[str, ...]
    # allowed_read_roots：字段说明见该模型文档与工作流契约。
    allowed_read_roots: tuple[str, ...]
    # allowed_write_roots：字段说明见该模型文档与工作流契约。
    allowed_write_roots: tuple[str, ...]
    # budget：字段说明见该模型文档与工作流契约。
    budget: dict[str, int]
    # retry_feedback：字段说明见该模型文档与工作流契约。
    retry_feedback: str = ""
    # source_project_path：本轮共享只读源码树路径；为空表示只有任务工作区。
    source_project_path: str = ""
    # project_rules：项目规范正文，供 Agent 在修改前核对约束。
    project_rules: str = ""


class PromptDocument(BaseModel):
    """单份 Markdown Prompt 的版本和正文。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # version：字段说明见该模型文档与工作流契约。
    version: str = Field(min_length=1)
    # body：字段说明见该模型文档与工作流契约。
    body: str = Field(min_length=1)


class RenderedPrompt(BaseModel):
    """一次 Prompt 渲染结果及可审计标识。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # system_prompt：字段说明见该模型文档与工作流契约。
    system_prompt: str
    # prompt：字段说明见该模型文档与工作流契约。
    prompt: str
    # prompt_version：字段说明见该模型文档与工作流契约。
    prompt_version: str
    # prompt_hash：字段说明见该模型文档与工作流契约。
    prompt_hash: str


__all__ = ["PromptContext", "PromptDocument", "RenderedPrompt"]
