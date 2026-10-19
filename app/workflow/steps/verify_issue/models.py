"""08 独立验证的输入输出契约。"""

from __future__ import annotations

from typing import Any

from app.workflow.spec import FrozenModel


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
    normalized_log: dict[str, Any] = {}  # 用于 response 重放验证


class VerifyOutput(FrozenModel):
    """07 的输出。"""

    # passed：字段说明见该模型文档与工作流契约。
    passed: bool
    # reason：字段说明见该模型文档与工作流契约。
    reason: str = ""
    # mode：实际执行的验证级别。
    mode: str = "custom"


__all__ = ["VerifyInput", "VerifyOutput"]
