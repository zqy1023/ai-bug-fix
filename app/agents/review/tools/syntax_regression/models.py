"""语法回归契约。"""

from __future__ import annotations

from pydantic import Field

from app.workflow.spec import FrozenModel


class SyntaxInput(FrozenModel):
    """仅编译存在的变更文件；删除必须由门禁和不可变基线共同确认。"""
    ai_workspace_path: str
    changed_files: list[str]
    baseline_manifest: dict[str, str] = Field(default_factory=dict)
    deleted_files: list[str] = Field(default_factory=list)


class SyntaxOutput(FrozenModel):
    passed: bool
    reason: str = ""
    mode: str = "syntax"
    checked_files: list[str] = Field(default_factory=list)

