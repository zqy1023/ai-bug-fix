"""ChangePlan：修复与审查共用的方案契约。

方案必须结构化，不能只活在散文里：``scope`` 说影响面，``edits`` 说要改哪些文件、
怎么改，``requires_human_authorization`` 说是否越过自动放行范围。有了它，
"给了方案却说无法修改"就有了一个真实的落盘通道（``apply_plan`` 插件）。
"""

from __future__ import annotations

from typing import Any, Literal

from app.workflow.spec import FrozenModel

ScopeTier = Literal["carrier", "project"]
EditAction = Literal["create", "update", "delete"]


class FileEdit(FrozenModel):
    """一个文件的确定改法。``content`` 是完整新内容，路径是工作区相对路径。"""

    path: str
    action: EditAction = "update"
    content: str = ""
    note: str = ""


class ChangePlan(FrozenModel):
    """一次修复的结构化方案。"""

    scope: ScopeTier = "carrier"
    edits: tuple[FileEdit, ...] = ()
    rationale: str = ""
    requires_human_authorization: bool = False

    @property
    def paths(self) -> tuple[str, ...]:
        """方案涉及的文件路径。"""
        return tuple(edit.path for edit in self.edits)

    def authorized(self) -> ChangePlan:
        """人工已授权的派生版本：project 影响面必须带授权标记。"""
        if self.scope == "carrier":
            return self
        return self.model_copy(update={"requires_human_authorization": True})


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