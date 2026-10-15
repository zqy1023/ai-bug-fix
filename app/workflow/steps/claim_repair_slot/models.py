"""04 领取修复资格的输入输出契约。"""

from __future__ import annotations

from typing import Literal

from app.workflow.spec import FrozenModel


class ClaimInput(FrozenModel):
    """04 的输入。"""

    run_id: str
    task_id: str
    biztype: str
    carrier: str
    source_repo_path: str
    problem_key: str


class ClaimOutput(FrozenModel):
    """04 的输出。"""

    claimed: bool
    result: Literal["claimed", "skipped"]


__all__ = ["ClaimInput", "ClaimOutput"]
