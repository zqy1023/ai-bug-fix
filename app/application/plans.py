"""ChangePlan：修复与审查共用的结构化分析契约。"""

from __future__ import annotations

from typing import Any, Literal

from app.workflow.spec import FrozenModel

ScopeTier = Literal["carrier", "project"]
EditAction = Literal["create", "update", "delete"]


class FileEdit(FrozenModel):
    """一个文件的确定改法。``content`` 是完整新内容，路径是工作区相对路径。"""

    # path：该模型字段用于承载对应契约中的结构化数据。
    path: str
    # action：该模型字段用于承载对应契约中的结构化数据。
    action: EditAction = "update"
    # content：该模型字段用于承载对应契约中的结构化数据。
    content: str = ""
    # note：该模型字段用于承载对应契约中的结构化数据。
    note: str = ""


class ChangePlan(FrozenModel):
    """一次修复的结构化方案。"""

    # scope：该模型字段用于承载对应契约中的结构化数据。
    scope: ScopeTier = "carrier"
    # edits：该模型字段用于承载对应契约中的结构化数据。
    edits: tuple[FileEdit, ...] = ()
    # rationale：该模型字段用于承载对应契约中的结构化数据。
    rationale: str = ""

    @property
    def paths(self) -> tuple[str, ...]:
        """方案涉及的文件路径。"""
        return tuple(edit.path for edit in self.edits)


def plan_of(payload: dict[str, Any]) -> ChangePlan | None:
    """从模型原始输出里取方案。结构非法或缺失都返回空，而不是让整轮崩掉。"""
    raw = payload.get("change_plan")
    if not isinstance(raw, dict):
        return None
    try:
        plan = ChangePlan.model_validate(raw)
    except Exception:
        return None
    # 只有理由、没有待改文件的"方案"无法落盘，按无方案处理
    return plan if plan.edits else None


__all__ = ["ChangePlan", "EditAction", "FileEdit", "ScopeTier", "plan_of"]
