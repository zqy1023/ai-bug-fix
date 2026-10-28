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
import re
import shutil
import stat
import subprocess
import threading
import uuid
import time
from collections.abc import Callable
from pathlib import Path

from git import Repo
from git.exc import GitCommandError, InvalidGitRepositoryError, NoSuchPathError

from infrastructure.llm.config import RuntimeConfig


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
    on_progress: Callable[[dict[str, int]], None] | None = None,
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
        _sync_mirror(config, mirror, cancel_requested=cancel_requested, on_progress=on_progress)
    finally:
        _SYNC_LOCK.release()
    return mirror


def _sync_mirror(
    config: RuntimeConfig,
    mirror: Path,
    *,
    cancel_requested: Callable[[], bool] | None = None,
    on_progress: Callable[[dict[str, int]], None] | None = None,
) -> None:
    """克隆缺失镜像，或拉取并使工作树对齐远端默认分支。"""
    env = dict(os.environ)
    env.update(git_auth_env(config.git_username, config.git_password))
    remote = config.git_remote
    timeout = config.git_fetch_timeout_seconds
    if not (mirror / ".git").is_dir():
        _clone(config, mirror, env, remote, timeout, cancel_requested=cancel_requested, on_progress=on_progress)
        return
    try:
        repo = Repo(mirror)
    except (InvalidGitRepositoryError, NoSuchPathError) as error:
        raise SourceRepoError(f"源仓库镜像不是有效 Git 仓库: {_display(mirror)}") from error
    try:
        origin_url = _origin_url(repo, remote)
        if origin_url is None:
            # 读取失败时不能删除镜像：继续用现有镜像 fetch，让真实 Git 错误
            # 在 fetch 阶段显式暴露，而不是静默丢失本地镜像与历史。
            _logger.warning(
                "无法确认镜像远程地址，跳过镜像重建并继续同步: %s",
                _display(mirror),
            )
        elif origin_url != config.source_url:
            repo.close()
            _force_remove(mirror)
            _clone(config, mirror, env, remote, timeout, cancel_requested=cancel_requested, on_progress=on_progress)
            return
        initial_bytes = _pack_bytes(mirror)

        def fetch_progress(elapsed: int) -> None:
            if on_progress is not None:
                on_progress({"downloaded_bytes": max(0, _pack_bytes(mirror) - initial_bytes),
                             "elapsed_seconds": elapsed, "timeout_seconds": timeout, "fetching": 1})

        _run_git(["fetch", "--prune", remote], mirror, env, timeout,
                 cancel_requested=cancel_requested, on_progress=fetch_progress)
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


def _pack_bytes(repository: Path) -> int:
    """读取已写入的 Git pack 大小；下载期间临时文件可能被原子替换。"""
    total = 0
    for path in (repository / ".git/objects/pack").glob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            continue
    return total


def _clone(
    config: RuntimeConfig,
    mirror: Path,
    env: dict[str, str],
    remote: str,
    timeout: int,
    *,
    cancel_requested: Callable[[], bool] | None = None,
    on_progress: Callable[[dict[str, int]], None] | None = None,
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
        def clone_progress(elapsed: int) -> None:
            if on_progress is None:
                return
            downloaded = _pack_bytes(temporary)
            on_progress({"downloaded_bytes": downloaded, "elapsed_seconds": elapsed,
                         "timeout_seconds": config.git_clone_timeout_seconds})

        _run_git(command, None, env, config.git_clone_timeout_seconds,
                 cancel_requested=cancel_requested, on_progress=clone_progress)
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


def _origin_url(repo: Repo, remote: str) -> str | None:
    """读取源远程 URL。

    只有"远程确实不存在"才返回空串；读取失败（索引/锁/进程异常等）返回
    ``None``，调用方据此跳过镜像重建，不能把瞬时读失败误判成 URL 变更而
    删除并重新克隆整个镜像。
    """
    try:
        return repo.git.remote("get-url", remote).strip()
    except GitCommandError as error:
        detail = str(getattr(error, "stderr", "") or error)
        if getattr(error, "status", None) in {1, 2} and "no such remote" in detail.casefold():
            return ""
        _logger.warning(
            "读取源远程 URL 失败，无法确认镜像来源 remote=%s: %s",
            remote, detail.strip()[-500:],
        )
        return None
    except Exception as error:
        _logger.warning(
            "读取源远程 URL 异常，无法确认镜像来源 remote=%s error=%s",
            remote, type(error).__name__, exc_info=True,
        )
        return None


def _run_git(
    args: list[str],
    cwd: Path | None,
    env: dict[str, str],
    timeout: int,
    *,
    cancel_requested: Callable[[], bool] | None = None,
    on_progress: Callable[[int], None] | None = None,
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
        last_progress = 0.0
        while True:
            now = time.monotonic()
            if on_progress and now - last_progress >= 5:
                last_progress = now
                try:
                    on_progress(int(now - started))
                except Exception:
                    _logger.warning("无法记录源码同步进度", exc_info=True)
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
        detail = (stderr or stdout or "").strip()
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
    """记录命令参数：URL 凭据剥掉，疑似令牌的 argv 值掩掉。"""
    redacted: list[str] = []
    for arg in args:
        value = _redact_url(str(arg))
        redacted.append("***" if _looks_like_token(value) else value)
    return redacted


def _redact_url(value: str) -> str:
    """记录 URL 时剥掉 userinfo 凭据（https://user:pass@host → https://***@host）。"""
    if "@" not in value or "://" not in value:
        return value
    return re.sub(r"://[^/@]+@", "://***@", value)


def _looks_like_token(value: str) -> bool:
    """判断一段 argv 是否疑似访问令牌。

    保守口径：整段是 40+ 位 base64/base64url/hex 连续串才算；纯小写 hex 的
    40/64 位通常是 Git SHA-1/SHA-256，不是机密，保留以便核对提交。
    """
    if len(value) < 40 or not re.fullmatch(r"[A-Za-z0-9/+_=-]+", value):
        return False
    return not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value)


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
