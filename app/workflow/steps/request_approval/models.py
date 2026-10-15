"""人工审批的输入输出契约。"""

from __future__ import annotations

from typing import Literal

from app.workflow.spec import FrozenModel
from pydantic import Field


class ApprovalInput(FrozenModel):
    """request_approval 的输入。"""

    task_id: str
    decision: Literal["PENDING", "APPROVE", "REJECT"] = "PENDING"
    carrier: str
    ai_workspace_path: str
    changed_files: list[str]
    baseline_manifest: dict[str, str] = Field(default_factory=dict)
    baseline_dir: str
    authorized_files: list[str] = Field(default_factory=list)


class ApprovalOutput(FrozenModel):
    """request_approval 的输出。"""

    decision: Literal["PENDING", "APPROVE", "REJECT"]
    passed: bool = False
    reason: str = ""


__all__ = ["ApprovalInput", "ApprovalOutput"]
