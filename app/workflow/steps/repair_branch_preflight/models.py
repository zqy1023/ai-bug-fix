"""修复分支预检的输入输出契约。"""

from __future__ import annotations

from typing import Literal

from app.workflow.spec import FrozenModel


class PreflightInput(FrozenModel):
    """只读检查同一问题是否已有未合并修复分支。"""

    # task_id：本任务标识。
    task_id: str
    # biztype：业务类型。
    biztype: str
    # carrier：运输商标识。
    carrier: str
    # source_repo_path：源 Git 仓库路径。
    source_repo_path: str
    # problem_key：规范化问题指纹。
    problem_key: str


class PreflightOutput(FrozenModel):
    """预检结论。通过只表示当前没有未合并分支，不表示已领取修复资格。"""

    # clear：当前没有未合并的同问题修复分支。
    clear: bool
    # result：业务处理结论。
    result: Literal["clear", "skipped"]
    # repair_branch：命中的未合并分支；没有则为空。
    repair_branch: str = ""


__all__ = ["PreflightInput", "PreflightOutput"]
