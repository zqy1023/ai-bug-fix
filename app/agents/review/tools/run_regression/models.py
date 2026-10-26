"""09 准生产回归的输入输出契约。"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.workflow.spec import FrozenModel


class RegressionInput(FrozenModel):
    """08 的输入。"""
    previous_result: dict[str, Any] = Field(default_factory=dict)

    # task_id：字段说明见该模型文档与工作流契约。
    task_id: str = ""
    # carrier：字段说明见该模型文档与工作流契约。
    carrier: str
    # ai_workspace_path：字段说明见该模型文档与工作流契约。
    ai_workspace_path: str
    # changed_files：字段说明见该模型文档与工作流契约。
    changed_files: list[str]
    business_verification_status: str = "unknown"
    normalized_log: dict[str, Any] = Field(default_factory=dict)
    baseline_dir: str = ""
    source_project_path: str = ""


class RegressionOutput(FrozenModel):
    failure_kind: str = ""
    input_id: str = ""
    reused: bool = False
    """08 的输出。"""

    # passed：字段说明见该模型文档与工作流契约。
    passed: bool
    evidence_version: dict[str, Any] = Field(default_factory=dict)
    # reason：字段说明见该模型文档与工作流契约。
    reason: str = ""
    # mode：实际执行的验证级别。
    mode: str = "regression"
    # command_count：字段说明见该模型文档与工作流契约。
    command_count: int = 0
    status: str = "unverified"
    commands: list[dict[str, Any]] = Field(default_factory=list)
    report_path: str = ""
    request_regression: dict[str, Any] = Field(default_factory=dict)
    supplemental_verification: dict[str, Any] = Field(default_factory=dict)


__all__ = ["RegressionInput", "RegressionOutput"]
