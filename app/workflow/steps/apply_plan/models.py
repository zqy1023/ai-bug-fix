"""落盘方案的输入输出契约。"""

from __future__ import annotations

from app.application.plans import ChangePlan
from app.workflow.spec import FrozenModel
from pydantic import Field


class ApplyPlanInput(FrozenModel):
    """apply_plan 的输入：人工决定 + 待落盘方案 + 工作区坐标。"""

    # task_id：本任务标识。
    task_id: str = ""
    # carrier：运输商标识。
    carrier: str = ""
    # ai_workspace_path：隔离 AI 工作区路径。
    ai_workspace_path: str = ""
    # decision：审批决定。
    decision: str = "PENDING"
    # change_plan：结构化变更方案。
    change_plan: ChangePlan | None = None


class ApplyPlanOutput(FrozenModel):
    """落盘结果。``authorized_files`` 回写主图，供后续门禁按文件放行。"""

    # applied：方案是否已落盘。
    applied: bool = False
    # written_files：实际写入文件清单。
    written_files: list[str] = Field(default_factory=list)
    # skipped：跳过处理的项目。
    skipped: list[str] = Field(default_factory=list)
    # authorized_files：已授权额外文件范围。
    authorized_files: list[str] = Field(default_factory=list)
    # scope：变更范围。
    scope: str = "carrier"
    # reason：处理原因。
    reason: str = ""


__all__ = ["ApplyPlanInput", "ApplyPlanOutput"]