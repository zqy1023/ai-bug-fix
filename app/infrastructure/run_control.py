"""工作台运行控制信号的持久化读取。"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from config.settings import settings


def _control_flags(runtime_dir: str | Path, run_id: str) -> tuple[bool, bool]:
    """返回 (pause_requested, cancel_requested)；控制库不可用时不伪造控制请求。"""
    database = Path(runtime_dir) / settings.workbench_control_db_name
    if not run_id or not database.is_file():
        return False, False
    try:
        with closing(sqlite3.connect(
            str(database),
            timeout=settings.workbench_control_read_timeout_seconds,
        )) as connection:
            row = connection.execute(
                "SELECT pause_requested, cancel_requested FROM run_control WHERE run_id = ?", (run_id,)
            ).fetchone()
            return (bool(row[0]), bool(row[1])) if row else (False, False)
    except sqlite3.Error:
        return False, False


def pause_requested(runtime_dir: str | Path, run_id: str) -> bool:
    """读取持久化暂停意图。"""
    return _control_flags(runtime_dir, run_id)[0]


def cancellation_requested(runtime_dir: str | Path, run_id: str) -> bool:
    """读取持久化取消意图；不存在控制库时视为未请求。"""
    return _control_flags(runtime_dir, run_id)[1]


__all__ = ["cancellation_requested", "pause_requested"]
