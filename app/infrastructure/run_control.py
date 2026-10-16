"""工作台发出的运行控制信号读取器。"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def cancellation_requested(runtime_dir: str | Path, run_id: str) -> bool:
    """读取持久化取消意图；不存在控制库时视为未请求。"""
    database = Path(runtime_dir) / "workbench-control.sqlite3"
    if not run_id or not database.is_file():
        return False
    try:
        with sqlite3.connect(str(database), timeout=1) as connection:
            row = connection.execute(
                "SELECT cancel_requested FROM run_control WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            return bool(row and row[0])
    except sqlite3.Error:
        return False


__all__ = ["cancellation_requested"]
