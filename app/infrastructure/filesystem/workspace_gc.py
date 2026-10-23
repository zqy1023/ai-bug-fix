"""过期工作区回收。

``runtime/workspaces`` 保存的是可重建缓存：共享源码树、任务隔离区、回归克隆
和交付临时克隆。审计事实在 ``runtime/runs``，因此终态执行的工作区可以按保留
策略回收，避免每轮 50–100 MB 的磁盘占用无限增长。

在任务收尾及服务运行期间定时执行，只删除已终态执行或超过 TTL 的目录；运行中的执行不会
被误删。删除失败（例如 Windows 只读对象或环境删除守卫）只记日志，不阻断启动。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app.infrastructure.filesystem.locking import shared_cache_lock
from app.infrastructure.records.lifecycle import LifecycleStore

logger = logging.getLogger(__name__)
_TERMINAL = {"succeeded", "partial_failed", "failed", "cancelled", "manual", "skipped"}

_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
_TERMINAL_REPORTS = ("final.json", "run_failure.json")


def _force_remove(path: Path) -> bool:
    """删除目录，兼容只读对象；返回是否成功。"""
    def onerror(function, failed_path, exc_info):
        try:
            os.chmod(failed_path, stat.S_IWRITE)
            function(failed_path)
        except OSError:
            raise exc_info[1]

    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path, onerror=onerror)
        return True
    except (OSError, SystemExit):
        logger.warning("工作区回收失败: %s", path, exc_info=True)
        return False


def _run_id_of(group: str, name: str) -> str:
    if group == "shared":
        return name if _RUN_ID.fullmatch(name) else ""
    prefix = name.split("-", 1)[0]
    return prefix if _RUN_ID.fullmatch(prefix) else ""


def _active_run(state: dict[str, Any]) -> bool:
    return bool(state.get("worker_active") or state.get("status") in {
        "queued", "running", "retrying", "pause_requested", "cancel_requested",
    })


def _is_terminal(runs_root: Path, run_id: str) -> bool:
    run_dir = runs_root / run_id
    if not run_dir.is_dir():
        return True
    lifecycle = LifecycleStore(runs_root.parent)
    state = lifecycle.read_run(run_id)
    if state.get("worker_active"):
        return False
    if state and state.get("status") not in _TERMINAL:
        return False
    # 旧 final 报告不能覆盖仍在等待审批或已恢复运行的任务。
    for task in (run_dir / "tasks").glob("*"):
        task_state = lifecycle.read_task(run_id, task.name)
        if task_state and task_state.get("status") not in _TERMINAL:
            return False
    if state:
        return True
    return any((run_dir / "reports" / name).is_file() for name in _TERMINAL_REPORTS)


def _expired(path: Path, seconds: float, state: dict[str, Any]) -> bool:
    finished = state.get("finished_at")
    if finished:
        try:
            return time.time() - datetime.fromisoformat(str(finished)).timestamp() >= seconds
        except ValueError:
            return False
    return _older_than(path, seconds)


def _safe_child(root: Path, path: Path) -> bool:
    # 删除前校验最终绝对路径，拒绝符号链接和 Windows junction 外逃。
    return not path.is_symlink() and path.resolve().is_relative_to(root.resolve())


def _older_than(path: Path, seconds: float) -> bool:
    try:
        return time.time() - path.stat().st_mtime >= seconds
    except OSError:
        return False


def prune_workspaces(
    workspace_root: str | Path,
    runs_root: str | Path,
    *,
    retention_days: int,
    delivery_ttl_hours: int,
) -> dict[str, int]:
    """回收过期工作区，返回各组删除数量。

    - ``delivery``：交付临时克隆，超过 TTL 即删（失败清理留下的残留）。
    - ``regression``：回归克隆，超过 TTL 即删。
    - ``ai``：任务真正结束后按任务保留期回收。
    - ``shared``：仅回收所有引用已结束且超过保留期的基线。
    """
    root = Path(workspace_root).resolve()
    runs = Path(runs_root).resolve()
    if not root.is_dir():
        return {}
    retention = max(0, int(retention_days)) * 86400
    ttl = max(0, int(delivery_ttl_hours)) * 3600
    lifecycle = LifecycleStore(runs.parent)
    removed: dict[str, int] = {}
    # 无法从旧交付目录名反推 run；存在活动 worker 时保守保留临时克隆。
    active_worker = any(
        _active_run(lifecycle.read_run(run.name))
        for run in runs.iterdir() if run.is_dir() and _RUN_ID.fullmatch(run.name)
    ) if runs.is_dir() else False
    with shared_cache_lock(root / "shared"):
        for group in ("delivery", "regression", "ai"):
            directory = root / group
            if not directory.is_dir():
                continue
            count = 0
            for item in directory.iterdir():
                if not item.is_dir() or not _safe_child(root, item):
                    continue
                if group in {"delivery", "regression"}:
                    eligible = not active_worker and _older_than(item, ttl)
                else:
                    run_id = _run_id_of(group, item.name)
                    if not run_id:
                        continue
                    state = lifecycle.read_run(run_id)
                    eligible = _is_terminal(runs, run_id) and _expired(item, retention, state)
                    task_name = item.name[len(run_id) + 1:]
                    for task in (runs / run_id / "tasks").glob("*"):
                        if task.name.casefold() != task_name:
                            continue
                        task_state = lifecycle.read_task(run_id, task.name)
                        # 本轮其它任务仍运行时，已结束任务也可按自己的保留期回收。
                        eligible = (task_state.get("status") in _TERMINAL
                                    and (task_state.get("workspace_released") or _is_terminal(runs, run_id))
                                    and _expired(item, retention, task_state))
                        break
                if eligible and _force_remove(item):
                    count += 1
            removed[group] = count

        shared = root / "shared"
        refs = shared / "refs"
        protected: set[str] = set()
        damaged_reference = False
        for reference in refs.glob("*.json"):
            if not _safe_child(root, reference):
                damaged_reference = True
                continue
            try:
                cache = json.loads(reference.read_text(encoding="utf-8"))["cache"]
                if not re.fullmatch(r"cache-[0-9a-f]{64}", cache):
                    raise ValueError("无效的缓存键")
            except (OSError, ValueError, KeyError, TypeError):
                # 无法证明无人使用时保留缓存，避免坏引用触发错误删除。
                damaged_reference = True
                logger.warning("共享源码引用损坏，跳过缓存回收: %s", reference)
                continue
            run_id = reference.stem
            if (_RUN_ID.fullmatch(run_id) and _is_terminal(runs, run_id)
                    and _expired(reference, retention, lifecycle.read_run(run_id))):
                try:
                    reference.unlink()
                    continue
                except OSError:
                    logger.warning("共享源码引用回收失败: %s", reference, exc_info=True)
            protected.add(cache)
        count = 0
        for item in shared.iterdir():
            if not item.is_dir() or not _safe_child(root, item) or item == refs:
                continue
            eligible = False
            if re.fullmatch(r"cache-[0-9a-f]{64}", item.name):
                eligible = not damaged_reference and item.name not in protected
            elif item.name.startswith("ai-replay-workspace-") or ".tmp-" in item.name:
                eligible = _older_than(item, ttl)
            elif _RUN_ID.fullmatch(item.name):
                eligible = (_is_terminal(runs, item.name)
                            and _expired(item, retention, lifecycle.read_run(item.name)))
            if eligible and _force_remove(item):
                count += 1
        removed["shared"] = count
    if any(removed.values()):
        logger.info("回收过期工作区: %s", removed)
    return removed


def collect_configured_workspaces(config: Any = None) -> dict[str, int]:
    """收尾和定时任务共用的回收入口；失败只记日志，不覆盖业务结果。"""
    try:
        if config is None:
            from app.infrastructure.config import load_config
            config = load_config()
        if not config.workspace_gc_enabled:
            return {}
        return prune_workspaces(
            config.workspace_root, config.runtime_dir / "runs",
            retention_days=config.workspace_retention_days,
            delivery_ttl_hours=config.delivery_workspace_ttl_hours,
        )
    except Exception:
        logger.exception("工作区回收失败")
        return {}


def start_workspace_collector(config: Any) -> tuple[threading.Event, threading.Thread]:
    """启动可停止的后台回收器，使用 Event.wait 避免阻塞服务退出。"""
    stop = threading.Event()

    def collect() -> None:
        collect_configured_workspaces(config)
        while not stop.wait(config.workspace_gc_interval_seconds):
            collect_configured_workspaces(config)

    thread = threading.Thread(target=collect, name="workspace-collector", daemon=True)
    thread.start()
    return stop, thread


__all__ = ["prune_workspaces", "collect_configured_workspaces", "start_workspace_collector"]
