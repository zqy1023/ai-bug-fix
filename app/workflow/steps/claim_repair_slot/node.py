"""04 按 Git 修复分支判断 biztype + carrier 是否可以进入本轮。"""

from __future__ import annotations

from git import Repo

from app.domain.errors import StepFailed
from app.domain.keys import repair_branch
from app.application.context import StepContext
from app.workflow.steps.claim_repair_slot.models import ClaimInput, ClaimOutput
from config.settings import settings


def run(ctx: StepContext, data: ClaimInput) -> ClaimOutput:
    """已有未合并修复分支时跳过，保证本轮处理的是干净状态。"""
    if ctx.services.source_repo is None and not data.source_repo_path:
        raise StepFailed("源仓库未配置，无法判断修复分支")
    source = data.source_repo_path or str(ctx.services.source_repo)
    branch = repair_branch(data.biztype, data.carrier, settings.repair_branch_prefix)
    try:
        repo = Repo(source, search_parent_directories=True)
    except Exception as error:
        raise StepFailed(f"不是有效的本地 Git 仓库: {source}") from error
    pending = _pending_branch(repo, branch, settings.default_git_remote)
    if pending:
        raise StepFailed(
            f"存在未合并的修复分支，跳过本轮任务: {pending}",
            {"claimed": False, "result": "skipped", "repair_branch": pending},
            result="skipped",
        )

    # 分支检查与本轮资格是两个不同的约束。分支不存在并不代表本轮尚未
    # 为同一运输商领取过另一个问题的修复资格。
    eligibility = ctx.services.eligibility
    if eligibility is None:
        raise StepFailed("修复资格服务未装配，拒绝绕过本轮去重门禁")
    try:
        claimed = eligibility.claim(
            data.run_id,
            data.biztype,
            data.carrier,
            data.problem_key,
        )
    except Exception as error:
        raise StepFailed(f"领取本轮修复资格失败: {error}") from error
    if not claimed:
        raise StepFailed(
            f"本轮已为该运输商领取过自动修复资格: {data.biztype}/{data.carrier}",
            {"claimed": False, "result": "skipped"},
            result="skipped",
        )
    return ClaimOutput(claimed=True, result="claimed")


def _pending_branch(repo: Repo, branch: str, remote_name: str) -> str:
    """返回尚未合入基准分支的本地或远程修复分支名。"""
    base = _base_commit(repo, remote_name)
    names = {head.name for head in repo.heads}
    remote = f"{remote_name}/{branch}"
    if remote in {ref.name for ref in repo.refs}:
        names.add(remote)
    for name in (branch, remote):
        if name not in names:
            continue
        try:
            repo.git.merge_base("--is-ancestor", name, base)
        except Exception:
            return name
    return ""


def _base_commit(repo: Repo, remote_name: str) -> str:
    """修复分支应合入的基准。优先远程默认分支，没有 remote 时用本地 HEAD。"""
    remote_refs = {ref.name for ref in repo.refs if ref.name.startswith(f"{remote_name}/")}
    for name in (f"{remote_name}/main", f"{remote_name}/master"):
        if name in remote_refs:
            return name
    return "HEAD"


__all__ = ["run"]
