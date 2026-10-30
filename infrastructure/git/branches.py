"""修复分支的远端状态查询与本地残留清理。

交付状态一律以**远端分支**为准：

- 领取资格时直接查远端 ``refs/heads/<branch>`` 是否存在，不再依赖本地引用或
  本地缓存的 ``origin/<branch>``（后者只在每轮开跑前 fetch 一次，运行中会过期）。
- 推送成功并回读确认后，删除源仓库里由交付器自己写入的本地分支（仅"无法推送
  的正式交付"会产生），避免本地引用变成第二套状态源。

本地开发模式（源仓库没有配置远程）没有远端状态可查，调用方需自行退回本地判定。
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Mapping
from contextlib import nullcontext
from typing import Any

from git import Repo
from git.exc import GitCommandError

_logger = logging.getLogger(__name__)

# 单次远端查询的超时；超时按查询失败处理，由调用方决定重试。
DEFAULT_QUERY_TIMEOUT_SECONDS = 60


def _environment(repo: Repo, env: Mapping[str, str] | None):
    """为 Git 子进程注入 HTTPS 凭据；凭据不写入仓库配置。"""
    merged = {
        str(key): str(value)
        for key, value in (env or {}).items()
        if value is not None
    }
    return repo.git.custom_environment(**merged) if merged else nullcontext()


def remote_branch_head(
    repo: Repo,
    remote_name: str,
    branch: str,
    *,
    env: Mapping[str, str] | None = None,
    timeout_seconds: int = DEFAULT_QUERY_TIMEOUT_SECONDS,
) -> str | None:
    """查询远端分支当前指向的提交；远端没有该分支时返回 ``None``。

    直接 ``ls-remote`` 远端，不复用 ``refs/remotes/<remote>/<branch>``：那是本地
    缓存，推送方（包括本进程自己）用 URL 推送时不会更新它。
    """
    target = f"refs/heads/{branch}"
    with _environment(repo, env):
        output = _ls_remote(repo, remote_name, target, timeout_seconds)
    for line in str(output).splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == target:
            return parts[0]
    return None


def _ls_remote(
    repo: Repo, remote_name: str, target: str, timeout_seconds: int
) -> str:
    """执行 ``ls-remote``，超时按失败处理。

    GitPython 的 ``kill_after_timeout`` 只在 POSIX 上实现：Windows 上传入这个
    参数会直接抛 ``GitCommandError``（"kill_after_timeout feature is not
    supported on Windows"），所有远端分支探测——领取资格、交付回读、审批清理
    都靠它——在 Windows 上一律失败。这里捕获这个特定错误并改成自己等进程、
    按超时 kill，跨平台行为一致。GitPython 在执行前就拒绝该参数，所以不会
    出现"命令跑了一半再重试"。
    """
    options: dict[str, Any] = {}
    if timeout_seconds and timeout_seconds > 0:
        options["kill_after_timeout"] = int(timeout_seconds)
    try:
        return str(repo.git.ls_remote("--heads", remote_name, target, **options))
    except GitCommandError as error:
        if "kill_after_timeout" not in str(error):
            raise
    process = repo.git.ls_remote("--heads", remote_name, target, as_process=True)
    try:
        stdout, _ = process.communicate(
            timeout=timeout_seconds if timeout_seconds and timeout_seconds > 0 else None
        )
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise
    if process.returncode:
        raise subprocess.CalledProcessError(
            process.returncode, ["git", "ls-remote", "--heads", remote_name, target]
        )
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    return str(stdout or "")


def remote_branch_exists(
    repo: Repo,
    remote_name: str,
    branch: str,
    *,
    env: Mapping[str, str] | None = None,
    timeout_seconds: int = DEFAULT_QUERY_TIMEOUT_SECONDS,
) -> bool:
    """远端是否存在同名分支（分支存在即视为该问题已被占用）。"""
    return remote_branch_head(
        repo,
        remote_name,
        branch,
        env=env,
        timeout_seconds=timeout_seconds,
    ) is not None


def delete_local_branch(
    repo: Repo,
    branch: str,
    *,
    expected_commit: str = "",
    source: str = "交付",
) -> bool:
    """删除源仓库里的本地修复分支，返回是否真的删掉了。

    只删指向期望提交的分支：本地引用若被别的东西改过，说明存在本工具不知道的
    状态，保留并告警，绝不静默丢掉别人的工作。
    """
    head = next((item for item in repo.heads if item.name == branch), None)
    if head is None:
        return False
    if expected_commit and head.commit.hexsha != expected_commit:
        _logger.warning(
            "%s：本地分支指向的提交与已发布提交不一致，保留不删 branch=%s local=%s expected=%s",
            source,
            branch,
            head.commit.hexsha,
            expected_commit,
        )
        return False
    try:
        active = "" if repo.head.is_detached else repo.active_branch.name
    except (TypeError, ValueError):
        active = ""
    if active == branch:
        _logger.warning("%s：本地分支正被检出，跳过删除 branch=%s", source, branch)
        return False
    try:
        repo.git.branch("-D", branch)
    except Exception:
        _logger.warning("%s：删除本地修复分支失败 branch=%s", source, branch, exc_info=True)
        return False
    return True


__all__ = [
    "DEFAULT_QUERY_TIMEOUT_SECONDS",
    "delete_local_branch",
    "remote_branch_exists",
    "remote_branch_head",
]
