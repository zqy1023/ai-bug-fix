"""快照之后、隔离工作区之前，只读跳过已有未合并修复分支的任务。"""

from __future__ import annotations

from core.errors import StepFailed
from core.contracts import StepContext
from workflow.stages.claim_slot.node import pending_repair_branch
from .models import PreflightInput, PreflightOutput
from config.settings import settings


def run(ctx: StepContext, data: PreflightInput) -> PreflightOutput:
    """远端已有该问题的修复分支时直接跳过，避免白建工作区和白跑定位。

    这里不写 claim。预检和真正领取之间仍可能出现新的分支或并发领取，
    ``claim_repair_slot`` 必须保留最终检查。
    """
    if ctx.services.source_repo is None and not data.source_repo_path:
        raise StepFailed("源仓库未配置，无法判断修复分支")
    source = data.source_repo_path or str(ctx.services.source_repo)
    pending = pending_repair_branch(
        source,
        biztype=data.biztype,
        carrier=data.carrier,
        problem_key=data.problem_key or data.task_id.rsplit("__", 1)[-1],
        remote_name=ctx.services.git_remote or settings.default_git_remote,
        git_env=getattr(ctx.services, "git_env", None),
    )
    if pending:
        raise StepFailed(
            f"远端已存在修复分支，跳过本轮任务: {pending}（合并后请删除该分支）",
            {"clear": False, "result": "skipped", "repair_branch": pending},
            result="skipped",
        )
    return PreflightOutput(clear=True, result="clear")


__all__ = ["run"]

