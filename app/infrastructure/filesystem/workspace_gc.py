"""过期工作区回收。

``runtime/workspaces`` 保存的是可重建缓存：共享源码树、任务隔离区、回归克隆
和交付临时克隆。审计事实在 ``runtime/runs``，因此终态执行的工作区可以按保留
策略回收，避免每轮 50–100 MB 的磁盘占用无限增长。

只在服务启动时执行，且只删除已终态执行或超过 TTL 的目录；运行中的执行不会
被误删。删除失败（例如 Windows 只读对象或环境删除守卫）只记日志，不阻断启动。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import time
from pathlib import Path

logger = logging.getLogger(__name__)

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


def _is_terminal(runs_root: Path, run_id: str) -> bool:
    run_dir = runs_root / run_id
    if not run_dir.is_dir():
        # 记录已删除的孤儿工作区可以直接回收。
        return True
    reports = run_dir / "reports"
    return any((reports / name).is_file() for name in _TERMINAL_REPORTS)


def _older_than(path: Path, seconds: float) -> bool:
    try:
        return time.time() - path.stat().st_mtime >= seconds
    except OSError:
        return False


def prune_workspaces(
    workspace_root: str | Path,
    runs_root: str | Path,
    *,
    retention_days: int = 7,
    delivery_ttl_hours: int = 24,
) -> dict[str, int]:
    """回收过期工作区，返回各组删除数量。

    - ``delivery``：交付临时克隆，超过 TTL 即删（失败清理留下的残留）。
    - ``regression``：回归克隆，超过 TTL 即删。
    - ``shared`` / ``ai``：仅当对应执行已写终态报告且超过保留天数时删除。
    """
    root = Path(workspace_root)
    runs = Path(runs_root)
    if not root.is_dir():
        return {}

    retention_seconds = max(0, int(retention_days)) * 86400
    ttl_seconds = max(0, int(delivery_ttl_hours)) * 3600
    removed: dict[str, int] = {}

    for group in ("delivery", "regression"):
        directory = root / group
        if not directory.is_dir():
            continue
        count = 0
        for item in directory.iterdir():
            if item.is_dir() and _older_than(item, ttl_seconds) and _force_remove(item):
                count += 1
        removed[group] = count

    for group in ("shared", "ai"):
        directory = root / group
        if not directory.is_dir():
            continue
        count = 0
        for item in directory.iterdir():
            if not item.is_dir():
                continue
            # 重放工作区的临时镜像位于 shared 下，不属于任何 run；超过 TTL 回收。
            if group == "shared" and item.name.startswith("ai-replay-workspace-"):
                if _older_than(item, ttl_seconds) and _force_remove(item):
                    count += 1
                continue
            run_id = _run_id_of(group, item.name)
            if not run_id or not _is_terminal(runs, run_id):
                continue
            if _older_than(item, retention_seconds) and _force_remove(item):
                count += 1
        removed[group] = count

    if any(removed.values()):
        logger.info("回收过期工作区: %s", removed)
    return removed


__all__ = ["prune_workspaces"]
