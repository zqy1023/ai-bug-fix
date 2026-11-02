"""CLI、API、恢复和单步共用的全局执行槽；复用现有控制库。"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
import sqlite3

from infrastructure.storage.sqlite import open_sqlite


@contextmanager
def execution_scope(root: Path) -> Iterator[sqlite3.Connection]:
    connection = open_sqlite(root / "workbench-control.sqlite3", timeout=30, busy_timeout_ms=30000)
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS execution_scope ("
                           "scope TEXT PRIMARY KEY, run_id TEXT NOT NULL, token TEXT NOT NULL, expires_at TEXT NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS run_control (run_id TEXT PRIMARY KEY, "
                           "pause_requested INTEGER NOT NULL DEFAULT 0, cancel_requested INTEGER NOT NULL DEFAULT 0, "
                           "updated_at TEXT NOT NULL)")
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
