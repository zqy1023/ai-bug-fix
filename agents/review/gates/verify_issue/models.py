"""08 独立验证的输入输出契约。"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from core.contracts import FrozenModel


class VerifyInput(FrozenModel):
    """07 的输入。"""

    # task_id：字段说明见该模型文档与工作流契约。
    task_id: str
    # carrier：字段说明见该模型文档与工作流契约。
    carrier: str
    # problem_key：字段说明见该模型文档与工作流契约。
    problem_key: str
    # ai_workspace_path：字段说明见该模型文档与工作流契约。
    ai_workspace_path: str
    # changed_files：字段说明见该模型文档与工作流契约。
    changed_files: list[str]
    # source_project_path：共享只读源码树（已脱敏，仅供模型阅读）。
    source_project_path: str = ""
    # source_repo_path：未脱敏的干净 Git 检出，重放需要完整依赖与二进制资产。
    source_repo_path: str = ""
    # baseline_dir：修复前的 carrier 源码快照，作为差分重放的基线。
    baseline_dir: str = ""
    normalized_log: dict[str, Any] = Field(default_factory=dict)


class VerifyOutput(FrozenModel):
    """07 的输出。"""

    # passed：字段说明见该模型文档与工作流契约。
    passed: bool
    input_id: str = ""
    evidence_version: dict[str, Any] = Field(default_factory=dict)
    # status：verified 表示完成业务验证；unverified 表示缺少业务证据，需人工确认。
    status: str = "unverified"
    # reason：字段说明见该模型文档与工作流契约。
    reason: str = ""
    # mode：实际执行的验证级别。
    mode: str = "custom"
    replay: dict[str, Any] = Field(default_factory=dict)
    report_path: str = ""
    verification: dict[str, Any] = Field(default_factory=dict)


__all__ = ["VerifyInput", "VerifyOutput"]
