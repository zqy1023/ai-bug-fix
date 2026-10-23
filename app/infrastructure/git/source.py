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
import time
from collections.abc import Callable
from pathlib import Path

from git import Repo
from git.exc import InvalidGitRepositoryError, NoSuchPathError

from app.infrastructure.config import RuntimeConfig
from app.infrastructure.redaction import redact_output


class SourceRepoError(RuntimeError):
    """源仓库克隆、拉取或本地镜像校验失败。"""


class SourceRepoCancelled(SourceRepoError):
    """Run 收到用户停止请求，已主动终止当前 Git 子进程。"""


class SourceRepoPaused(SourceRepoCancelled):
    """Run 请求暂停，Git 已退出并可从同步节点重新开始。"""


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
    *,
    cancel_requested: Callable[[], bool] | None = None,
) -> None:
    """把镜像工作树收敛到业务目录；老版本 Git 或不支持的路径只降级为完整检出。

    这里用非锥形模式：锥形模式总会保留仓库根目录下的文件（``README.md`` 等），
    镜像里没有必要出现仓库其它内容。非锥形模式下传入目录会被展开成递归匹配。
    """
    if not cone:
        return
    try:
        _run_git(
            ["sparse-checkout", "set", "--no-cone", f"/{cone}/"],
            mirror, env, timeout, cancel_requested=cancel_requested,
        )
    except SourceRepoCancelled:
        raise
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


def sync_source_repo(
    config: RuntimeConfig,
    *,
    cancel_requested: Callable[[], bool] | None = None,
) -> Path | None:
    """解析本轮源仓库路径；URL 模式先克隆或拉取受控镜像，可响应 Run 取消信号。"""
    if not config.source_url:
        return config.source_repo
    if config.source_repo is None:
        raise SourceRepoError("远程源仓库镜像目录未配置")
    mirror = Path(config.source_repo)
    while not _SYNC_LOCK.acquire(timeout=0.5):
        if cancel_requested and cancel_requested():
            raise SourceRepoCancelled("Run 已请求停止，正在等待源仓库同步锁")
    try:
        if cancel_requested and cancel_requested():
            raise SourceRepoCancelled("Run 已请求停止，源仓库同步未启动")
        _sync_mirror(config, mirror, cancel_requested=cancel_requested)
    finally:
        _SYNC_LOCK.release()
    return mirror


def _sync_mirror(
    config: RuntimeConfig,
    mirror: Path,
    *,
    cancel_requested: Callable[[], bool] | None = None,
) -> None:
    """克隆缺失镜像，或拉取并使工作树对齐远端默认分支。"""
    env = dict(os.environ)
    env.update(git_auth_env(config.git_username, config.git_password))
    remote = config.git_remote
    timeout = config.git_fetch_timeout_seconds
    if not (mirror / ".git").is_dir():
        _clone(config, mirror, env, remote, timeout, cancel_requested=cancel_requested)
        return
    try:
        repo = Repo(mirror)
    except (InvalidGitRepositoryError, NoSuchPathError) as error:
        raise SourceRepoError(f"源仓库镜像不是有效 Git 仓库: {_display(mirror)}") from error
    try:
        if _origin_url(repo, remote) != config.source_url:
            repo.close()
            _force_remove(mirror)
            _clone(config, mirror, env, remote, timeout, cancel_requested=cancel_requested)
            return
        _run_git(["fetch", "--prune", remote], mirror, env, timeout, cancel_requested=cancel_requested)
        try:
            _run_git(["remote", "set-head", remote, "--auto"], mirror, env, timeout, cancel_requested=cancel_requested)
        except SourceRepoCancelled:
            raise
        except SourceRepoError:
            # 部分服务端不暴露远端 HEAD；_default_branch 会退回 main/master。
            pass
        branch = _default_branch(mirror, remote, env, timeout, cancel_requested=cancel_requested)
        _run_git(["checkout", "-f", "-B", branch, f"{remote}/{branch}"], mirror, env, timeout, cancel_requested=cancel_requested)
        # 先清掉旧版本留下的整仓库残留，再把工作树收敛到业务目录。
        _run_git(["clean", "-fdx"], mirror, env, timeout, cancel_requested=cancel_requested)
        _apply_sparse_checkout(
            mirror, project_cone(config), env, timeout, cancel_requested=cancel_requested,
        )
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
    *,
    cancel_requested: Callable[[], bool] | None = None,
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
        command = ["clone", "--config=core.longpaths=true", "--origin", remote]
        if cone:
            command.append("--sparse")
        command.extend([config.source_url, str(temporary)])
        _run_git(command, None, env, timeout, cancel_requested=cancel_requested)
        _apply_sparse_checkout(temporary, cone, env, timeout, cancel_requested=cancel_requested)
        temporary.replace(mirror)
    except SourceRepoError:
        _force_remove(temporary)
        raise
    except Exception as error:
        _force_remove(temporary)
        raise SourceRepoError(f"源仓库克隆失败: {_display(config.source_url)}") from error


def _default_branch(
    mirror: Path,
    remote: str,
    env: dict[str, str],
    timeout: int,
    *,
    cancel_requested: Callable[[], bool] | None = None,
) -> str:
    """读取远端默认分支；符号引用缺失时退回 main/master。"""
    try:
        symbolic = _run_git(
            ["symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD"],
            mirror,
            env,
            timeout,
            cancel_requested=cancel_requested,
        ).strip()
    except SourceRepoCancelled:
        raise
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
        cancel_requested=cancel_requested,
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
    *,
    cancel_requested: Callable[[], bool] | None = None,
) -> str:
    """执行 Git 命令，失败信息保留原值。"""
    # 同时覆盖已有镜像的 checkout 和新镜像的首次检出，不依赖宿主机全局配置。
    command = ["git", "-c", "core.longpaths=true", *args]
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=os.name != "nt",
        )
        started = time.monotonic()
        while True:
            if cancel_requested and cancel_requested():
                raise SourceRepoCancelled("Run 已请求停止，已终止正在执行的 Git 命令")
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                stdout, stderr = process.communicate(timeout=min(0.5, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
    except SourceRepoCancelled:
        if process is not None:
            _terminate_git_process(process)
        raise
    except subprocess.TimeoutExpired as error:
        if process is not None:
            _terminate_git_process(process)
        raise SourceRepoError(
            f"Git 命令超时: git {' '.join(_redact_args(args))}"
        ) from error
    except OSError as error:
        raise SourceRepoError(f"无法执行 Git 命令: {type(error).__name__}") from error
    if process is None:
        raise SourceRepoError("Git 子进程未能启动")
    if process.returncode != 0:
        detail = redact_output((stderr or stdout or "").strip())
        raise SourceRepoError(
            f"Git 命令失败（git {' '.join(_redact_args(args))}）: {detail[-1500:]}"
        )
    return stdout


def _terminate_git_process(process: subprocess.Popen[str]) -> None:
    """Terminate this Git process tree and bound pipe draining on every platform."""
    if os.name == "nt":
        # git.exe launches remote helpers that inherit stdout/stderr. Killing only
        # the root leaves those pipes open, so communicate() can wait forever.
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=5, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            _logger.warning("终止 Git 进程树失败 pid=%s", process.pid)
    else:
        import signal
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        # Never wait without a deadline, including when a remote helper is stuck.
        _logger.warning("Git 管道回收超时 pid=%s", process.pid)


def _redact_args(args: list[str]) -> list[str]:
    """保留原始参数，兼容旧接口。"""
    return [_redact_url(arg) for arg in args]


def _redact_url(value: str) -> str:
    """日志保留原始命令参数，兼容旧接口。"""
    return value


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


__all__ = ["SourceRepoCancelled", "SourceRepoError", "git_auth_env", "sync_source_repo"]
