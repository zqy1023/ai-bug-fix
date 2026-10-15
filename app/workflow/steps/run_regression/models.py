"""09 准生产回归的输入输出契约。"""

from __future__ import annotations

from app.workflow.spec import FrozenModel


class RegressionInput(FrozenModel):
    """08 的输入。"""

    task_id: str = ""
    carrier: str
    ai_workspace_path: str
    changed_files: list[str]


class RegressionOutput(FrozenModel):
    """08 的输出。"""

    passed: bool
    reason: str = ""
    command_count: int = 0


__all__ = ["RegressionInput", "RegressionOutput"]
