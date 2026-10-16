"""09 准生产回归的输入输出契约。"""

from __future__ import annotations

from app.workflow.spec import FrozenModel


class RegressionInput(FrozenModel):
    """08 的输入。"""

    # task_id：字段说明见该模型文档与工作流契约。
    task_id: str = ""
    # carrier：字段说明见该模型文档与工作流契约。
    carrier: str
    # ai_workspace_path：字段说明见该模型文档与工作流契约。
    ai_workspace_path: str
    # changed_files：字段说明见该模型文档与工作流契约。
    changed_files: list[str]


class RegressionOutput(FrozenModel):
    """08 的输出。"""

    # passed：字段说明见该模型文档与工作流契约。
    passed: bool
    # reason：字段说明见该模型文档与工作流契约。
    reason: str = ""
    # command_count：字段说明见该模型文档与工作流契约。
    command_count: int = 0


__all__ = ["RegressionInput", "RegressionOutput"]
