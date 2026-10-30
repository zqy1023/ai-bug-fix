"""确认同一问题的 Git 修复分支不存在未合并冲突。

全局串行由启动入口保证；本节点不再维护 per-problem claim 表，
只保留远端分支检查，避免把“并发互斥”和“重复问题去重”混在一起。
"""

from __future__ import annotations

from collections.abc import Mapping

from git import Repo
from git.exc import GitCommandError

from core.errors import StepFailed
from core.keys import repair_branch
from core.contracts import StepContext
from infrastructure.git.branches import remote_branch_exists
from .models import ClaimInput, ClaimOutput
from config.settings import settings


def pending_repair_branch(
    source: str,
    *,
    biztype: str,
    carrier: str,
    problem_key: str,
    remote_name: str,
    git_env: Mapping[str, str] | None = None,
) -> str:
    """检查远端是否已存在同一问题的修复分支。没有则返回空字符串。

    判定口径是**远端分支是否存在**，不是"本地是否已有未合并分支"：本地候选分支
    在推送成功后会被删除，且 ``origin/<branch>`` 只是开跑前 fetch 的缓存，运行中
    别人推送的分支看不到。远端查询失败按可重试失败处理，不能当成"没有分支"放行。
    """
    branch = repair_branch(
        biztype,
        carrier,
        settings.repair_branch_prefix,
        problem_key,
    )
    try:
        repo = Repo(source, search_parent_directories=True)
    except Exception as error:
        raise StepFailed(f"不是有效的本地 Git 仓库: {source}") from error
    try:
        if remote_name in {remote.name for remote in repo.remotes}:
            try:
                exists = remote_branch_exists(
                    repo, remote_name, branch, env=git_env
                )
            except Exception as error:
                raise StepFailed(
                    f"无法查询远端修复分支，稍后重试: {branch}",
                    {"branch": branch, "remote": remote_name, "reason": "remote_query_failed"},
                    code="GIT_REMOTE_QUERY_FAILED",
                ) from error
            return branch if exists else ""
        # 本地开发模式没有远端状态可查，只能退回本地分支判定（含"是否已合入"）。
        return _pending_local(repo, branch, remote_name)
    finally:
        repo.close()


def _effective_problem_key(data: ClaimInput) -> str:
    """problem_key 缺失时的统一回退口径：task_id 的最后一段（问题指纹短哈希）。

    分支名（``repair_branch``）必须和 task_id 使用同一指纹段，否则同一个问题
    在预检和领取阶段会算出不同的分支名。
    """
    return data.problem_key or data.task_id.rsplit("__", 1)[-1]


def run(ctx: StepContext, data: ClaimInput) -> ClaimOutput:
    """远端已有同一问题的修复分支时跳过，保证本轮处理的是干净状态。

    这里不再抢占 SQLite claim：启动入口已保证全局串行，跨批次的重复问题
    由远端分支检查负责。
    """
    if ctx.services.source_repo is None and not data.source_repo_path:
        raise StepFailed("源仓库未配置，无法判断修复分支")
    source = data.source_repo_path or str(ctx.services.source_repo)
    problem_key = _effective_problem_key(data)
    branch = repair_branch(
        data.biztype,
        data.carrier,
        settings.repair_branch_prefix,
        problem_key,
    )
    pending = pending_repair_branch(
        source,
        biztype=data.biztype,
        carrier=data.carrier,
        problem_key=problem_key,
        remote_name=ctx.services.git_remote or settings.default_git_remote,
        git_env=getattr(ctx.services, "git_env", None),
    )
    if pending:
        raise StepFailed(
            f"远端已存在修复分支，跳过本轮任务: {pending}（合并后请删除该分支）",
            {"claimed": False, "result": "skipped", "repair_branch": pending},
            result="skipped",
        )

    return ClaimOutput(claimed=True, result="claimed")


def _pending_local(repo: Repo, branch: str, remote_name: str) -> str:
    """没有远端时的兜底：返回尚未合入基准分支的本地修复分支名。

    只认本地 ``refs/heads/<branch>``：没有远端时 ``origin/*`` 只能是历史残留，
    拿它当状态会永远拦住同一问题。
    """
    if branch not in {head.name for head in repo.heads}:
        return ""
    base = _base_commit(repo, remote_name)
    try:
        repo.git.merge_base("--is-ancestor", branch, base)
    except GitCommandError as error:
        # merge-base --is-ancestor: 0=已合入，1=未合入。只有 1 才代表
        # "存在未合并修复分支"；其它状态是 Git 本身故障，不能静默当成
        # 已合并而跳过清理，也不能把它误判成业务跳过。
        if getattr(error, "status", None) == 1:
            return branch
        raise StepFailed(
            "git 探测失败（可重试）",
            {
                "branch": branch,
                "base": base,
                "git_status": getattr(error, "status", None),
            },
        ) from error
    return ""


def _base_commit(repo: Repo, remote_name: str) -> str:
    """修复分支应合入的基准。优先远程默认分支，没有 remote 时用本地 HEAD。"""
    remote_refs = {ref.name for ref in repo.refs if ref.name.startswith(f"{remote_name}/")}
    for name in (f"{remote_name}/main", f"{remote_name}/master"):
        if name in remote_refs:
            return name
    return "HEAD"


__all__ = ["pending_repair_branch", "run"]
