"""08 独立验证的输入输出契约。"""

from __future__ import annotations

from typing import Any

from app.workflow.spec import FrozenModel


class VerifyInput(FrozenModel):
    """07 的输入。"""

    task_id: str
    carrier: str
    problem_key: str
    ai_workspace_path: str
    changed_files: list[str]
    normalized_log: dict[str, Any] = {}  # 用于 response 重放验证


class VerifyOutput(FrozenModel):
    """07 的输出。"""

    passed: bool
    reason: str = ""


__all__ = ["VerifyInput", "VerifyOutput"]
