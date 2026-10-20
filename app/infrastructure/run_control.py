"""工作台运行控制信号的持久化读取。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from config.settings import settings


def _control_flags(runtime_dir: str | Path, run_id: str) -> tuple[bool, bool]:
    """返回 (pause_requested, cancel_requested)；控制库不可用时不伪造控制请求。"""
    database = Path(runtime_dir) / settings.workbench_control_db_name
    if not run_id or not database.is_file():
        return False, False
    try:
        with sqlite3.connect(
            str(database),
            timeout=settings.workbench_control_read_timeout_seconds,
        ) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(run_control)")}
            names = [name for name in ("pause_requested", "cancel_requested") if name in columns]
            if not names:
                return False, False
            selected = ", ".join(names)
            row = connection.execute(
                f"SELECT {selected} FROM run_control WHERE run_id = ?", (run_id,)
            ).fetchone()
            if not row:
                return False, False
            values = dict(zip(names, row))
            return bool(values.get("pause_requested", 0)), bool(values.get("cancel_requested", 0))
    except sqlite3.Error:
        return False, False


def pause_requested(runtime_dir: str | Path, run_id: str) -> bool:
    """读取持久化暂停意图。"""
    return _control_flags(runtime_dir, run_id)[0]


def cancellation_requested(runtime_dir: str | Path, run_id: str) -> bool:
    """读取持久化取消意图；不存在控制库时视为未请求。"""
    return _control_flags(runtime_dir, run_id)[1]


__all__ = ["cancellation_requested", "pause_requested"]
