"""04 领取修复资格的输入输出契约。"""

from __future__ import annotations

from typing import Literal

from core.contracts import FrozenModel


class ClaimInput(FrozenModel):
    """04 的输入。"""

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


class ClaimOutput(FrozenModel):
    """04 的输出。"""

    # claimed：是否成功领取本轮修复资格。
    claimed: bool
    # result：业务处理结论。
    result: Literal["claimed", "skipped"]


__all__ = ["ClaimInput", "ClaimOutput"]
