"""落盘方案的输入输出契约。"""

from __future__ import annotations

from app.application.plans import ChangePlan
from app.workflow.spec import FrozenModel
from pydantic import Field


class ApplyPlanInput(FrozenModel):
    """apply_plan 的输入：人工决定 + 待落盘方案 + 工作区坐标。"""

    task_id: str = ""
    carrier: str = ""
    ai_workspace_path: str = ""
    decision: str = "PENDING"
    change_plan: ChangePlan | None = None


class ApplyPlanOutput(FrozenModel):
    """落盘结果。``authorized_files`` 回写主图，供后续门禁按文件放行。"""

    applied: bool = False
    written_files: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    authorized_files: list[str] = Field(default_factory=list)
    scope: str = "carrier"
    reason: str = ""


__all__ = ["ApplyPlanInput", "ApplyPlanOutput"]