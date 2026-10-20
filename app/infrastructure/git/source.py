"""远程源仓库镜像：按 ENV 配置克隆并同步到运行时目录。

本地路径模式直接使用开发机仓库；URL 模式由本模块维护
``runtime/source-repo``。每次装配工作流服务前执行 ``fetch --prune``，
让分支预检看到远端最新状态，同时把镜像工作树对齐到远端默认分支。
凭据只进入 Git 子进程环境，不写入镜像的 ``.git/config``。
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
import stat
import subprocess
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from git import Repo
from git.exc import InvalidGitRepositoryError, NoSuchPathError

from app.infrastructure.config import RuntimeConfig
from app.infrastructure.redaction import redact_output


class SourceRepoError(RuntimeError):
    """源仓库克隆、拉取或本地镜像校验失败。"""


# 同一进程内的运行串行同步镜像；不同进程共享镜像属于部署错误，不在处理范围。
_SYNC_LOCK = threading.Lock()
_logger = logging.getLogger(__name__)


def project_cone(config: RuntimeConfig) -> str:
    """返回镜像工作树应物化的业务目录（Git sparse-checkout 锥形路径）。

    镜像仍是完整 Git 仓库；只是工作树里除 ``AI_REPAIR_PROJECT_SUBDIR`` 之外的文件
    不再落盘。回归/交付工作区从这个镜像克隆时拿到的仍是完整树，因此锥形范围只影响
    磁盘占用，不影响下游 Git 操作。
    """
    configured = config.project_subdir
    if configured is None:
        return ""
    if configured.is_absolute():
        raise SourceRepoError("AI_REPAIR_PROJECT_SUBDIR 必须是相对仓库根目录的路径")
    relative = configured.as_posix().strip("/")
    return "" if relative in {"", "."} else relative


def _apply_sparse_checkout(
    mirror: Path,
    cone: str,
    env: dict[str, str],
    timeout: int,
) -> None:
    """把镜像工作树收敛到业务目录；老版本 Git 或不支持的路径只降级为完整检出。

    这里用非锥形模式：锥形模式总会保留仓库根目录下的文件（``README.md`` 等），
    镜像里没有必要出现仓库其它内容。非锥形模式下传入目录会被展开成递归匹配。
    """
    if not cone:
        return
    try:
        _run_git(["sparse-checkout", "set", "--no-cone", f"/{cone}/"], mirror, env, timeout)
    except SourceRepoError as error:
        # 稀疏检出只省磁盘。这里失败不该让整轮同步失败，但必须留下可查原因。
        _logger.warning("镜像稀疏检出失败，保留完整工作树: %s (%s)", _display(mirror), error)


def git_auth_env(username: str, password: str) -> dict[str, str]:
    """返回 Git 子进程的 HTTPS 凭据环境。

    通过 ``GIT_CONFIG_*`` 注入 ``http.extraHeader``，凭据既不出现在命令行参数里，
    也不会跟随 clone 写进 ``.git/config``。SSH 地址不需要该头，走 ssh-agent。
    """
    env = {"GIT_TERMINAL_PROMPT": "0"}
    if not password:
        return env
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    env.update(
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {token}",
        }
    )
    return env


def sync_source_repo(config: RuntimeConfig) -> Path | None:
    """解析本轮源仓库路径；URL 模式先克隆或拉取受控镜像。"""
    if not config.source_url:
        return config.source_repo
    if config.source_repo is None:
        raise SourceRepoError("远程源仓库镜像目录未配置")
    mirror = Path(config.source_repo)
    with _SYNC_LOCK:
        _sync_mirror(config, mirror)
    return mirror


def _sync_mirror(config: RuntimeConfig, mirror: Path) -> None:
    """克隆缺失镜像，或拉取并使工作树对齐远端默认分支。"""
    env = dict(os.environ)
    env.update(git_auth_env(config.git_username, config.git_password))
    remote = config.git_remote
    timeout = config.git_fetch_timeout_seconds
    if not (mirror / ".git").is_dir():
        _clone(config, mirror, env, remote, timeout)
        return
    try:
        repo = Repo(mirror)
    except (InvalidGitRepositoryError, NoSuchPathError) as error:
        raise SourceRepoError(f"源仓库镜像不是有效 Git 仓库: {_display(mirror)}") from error
    try:
        if _origin_url(repo, remote) != config.source_url:
            repo.close()
            _force_remove(mirror)
            _clone(config, mirror, env, remote, timeout)
            return
        _run_git(["fetch", "--prune", remote], mirror, env, timeout)
        try:
            _run_git(["remote", "set-head", remote, "--auto"], mirror, env, timeout)
        except SourceRepoError:
            # 部分服务端不暴露远端 HEAD；_default_branch 会退回 main/master。
            pass
        branch = _default_branch(mirror, remote, env, timeout)
        _run_git(["checkout", "-f", "-B", branch, f"{remote}/{branch}"], mirror, env, timeout)
        # 先清掉旧版本留下的整仓库残留，再把工作树收敛到业务目录。
        _run_git(["clean", "-fdx"], mirror, env, timeout)
        _apply_sparse_checkout(mirror, project_cone(config), env, timeout)
    except SourceRepoError:
        raise
    except Exception as error:
        raise SourceRepoError(f"源仓库镜像同步失败: {_display(mirror)}") from error
    finally:
        try:
            repo.close()
        except Exception:
            pass


def _clone(
    config: RuntimeConfig,
    mirror: Path,
    env: dict[str, str],
    remote: str,
    timeout: int,
) -> None:
    """克隆到临时目录后原子替换，避免中断留下半成品镜像。

    ``--sparse`` 让克隆一开始就不物化整仓库，随后只检出业务目录；
    镜像的仓库对象和引用仍是完整的一份。
    """
    mirror.parent.mkdir(parents=True, exist_ok=True)
    if mirror.exists():
        _force_remove(mirror)
    temporary = mirror.with_name(f".{mirror.name}.tmp-{uuid.uuid4().hex}")
    cone = project_cone(config)
    try:
        command = ["clone", "--origin", remote]
        if cone:
            command.append("--sparse")
        command.extend([config.source_url, str(temporary)])
        _run_git(command, None, env, timeout)
        _apply_sparse_checkout(temporary, cone, env, timeout)
        temporary.replace(mirror)
    except SourceRepoError:
        _force_remove(temporary)
        raise
    except Exception as error:
        _force_remove(temporary)
        raise SourceRepoError(f"源仓库克隆失败: {_display(config.source_url)}") from error


def _default_branch(mirror: Path, remote: str, env: dict[str, str], timeout: int) -> str:
    """读取远端默认分支；符号引用缺失时退回 main/master。"""
    try:
        symbolic = _run_git(
            ["symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD"],
            mirror,
            env,
            timeout,
        ).strip()
    except SourceRepoError:
        symbolic = ""
    prefix = f"{remote}/"
    if symbolic.startswith(prefix):
        return symbolic[len(prefix):]
    refs = _run_git(
        ["for-each-ref", "--format=%(refname:short)", f"refs/remotes/{remote}"],
        mirror,
        env,
        timeout,
    ).splitlines()
    for name in ("main", "master"):
        if f"{prefix}{name}" in refs:
            return name
    raise SourceRepoError(f"远程 {remote} 没有可用的默认分支")


def _origin_url(repo: Repo, remote: str) -> str:
    """读取源远程 URL；远程不存在时返回空串。"""
    try:
        return repo.git.remote("get-url", remote).strip()
    except Exception:
        return ""


def _run_git(
    args: list[str],
    cwd: Path | None,
    env: dict[str, str],
    timeout: int,
) -> str:
    """执行 Git 命令；失败信息先脱敏再抛出。"""
    command = ["git", *args]
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise SourceRepoError(
            f"Git 命令超时: git {' '.join(_redact_args(args))}"
        ) from error
    except OSError as error:
        raise SourceRepoError(f"无法执行 Git 命令: {type(error).__name__}") from error
    if completed.returncode != 0:
        detail = redact_output((completed.stderr or completed.stdout or "").strip())
        raise SourceRepoError(
            f"Git 命令失败（git {' '.join(_redact_args(args))}）: {detail[-1500:]}"
        )
    return completed.stdout


def _redact_args(args: list[str]) -> list[str]:
    """隐藏 URL 中的 userinfo，避免凭据进入错误信息。"""
    return [_redact_url(arg) for arg in args]


def _redact_url(value: str) -> str:
    """去掉 URL 的 username/password 部分。"""
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if not parts.scheme or (parts.username is None and parts.password is None):
        return value
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def _display(value: Path | str) -> str:
    """路径或 URL 的可记录形式。"""
    return _redact_url(str(value))


def _force_remove(path: Path) -> None:
    """删除文件或目录，兼容 Windows 只读文件。"""
    if not path.exists() and not path.is_symlink():
        return
    if path.is_file() or path.is_symlink():
        path.unlink()
        return

    def onerror(func, raw_path, _exc_info):
        os.chmod(raw_path, stat.S_IWRITE)
        func(raw_path)

    shutil.rmtree(path, onerror=onerror)


__all__ = ["SourceRepoError", "git_auth_env", "sync_source_repo"]