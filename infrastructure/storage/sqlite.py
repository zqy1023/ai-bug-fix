"""统一 SQLite 连接创建与基础连接参数。

各存储模块仍拥有自己的 Schema 和事务边界；本模块只统一连接参数，
避免每个存储实现重复维护 busy_timeout、row_factory 等基础行为。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def open_sqlite(
    path: str | Path,
    *,
    timeout: float = 30,
    busy_timeout_ms: int | None = None,
    check_same_thread: bool = True,
    row_factory: Any = None,
    journal_mode: str | None = None,
) -> sqlite3.Connection:
    """创建 SQLite 连接并统一应用显式指定的连接级选项。

    不自动创建业务表、不隐式开启事务，也不默认切换 journal_mode；
    Schema、事务和 WAL 策略仍由对应存储所有者决定。
    """
    database = Path(path)
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        str(database),
        timeout=timeout,
        check_same_thread=check_same_thread,
    )
    if row_factory is not None:
        connection.row_factory = row_factory
    if busy_timeout_ms is not None:
        connection.execute(f"PRAGMA busy_timeout={max(0, int(busy_timeout_ms))}")
    if journal_mode:
        # journal_mode 只接受 SQLite 支持的固定枚举，避免拼接任意 SQL。
        mode = journal_mode.upper()
        if mode not in {"DELETE", "TRUNCATE", "PERSIST", "MEMORY", "WAL", "OFF"}:
            connection.close()
            raise ValueError(f"不支持的 SQLite journal_mode: {journal_mode}")
        connection.execute(f"PRAGMA journal_mode={mode}")
    return connection


__all__ = ["open_sqlite"]
