"""跨进程安全的单轮修复资格登记。"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from threading import RLock


def _enable_wal(connection: sqlite3.Connection, database_path: Path) -> None:
    """并发进程首次打开同一数据库时，重试 WAL 模式切换的瞬时锁冲突。"""
    delays = (0.05, 0.1, 0.2, 0.4, 0.8)
    for attempt, delay in enumerate((*delays, None)):
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as error:
            message = str(error).lower()
            if "locked" not in message and "busy" not in message:
                raise
            if delay is None:
                raise sqlite3.OperationalError(
                    f"初始化 SQLite WAL 模式失败，数据库仍被其他连接占用: {database_path}"
                ) from error
            time.sleep(delay)


class ExecutionEligibility:
    """保证同一 run 内同一 biztype/carrier 只领取一次修复资格。

    SQLite 主键约束负责跨进程原子性；JSON 文件只作为旧版本的一次性迁移来源。
    """

    def __init__(self, root: str | Path | None = None) -> None:
        self._lock = RLock()
        self._legacy_path: Path | None = None
        if root is None:
            self._connection = sqlite3.connect(":memory:", check_same_thread=False, timeout=30)
        else:
            directory = Path(root).resolve()
            directory.mkdir(parents=True, exist_ok=True)
            self._legacy_path = directory / "eligibility.json"
            self._connection = sqlite3.connect(
                str(directory / "eligibility.sqlite3"),
                check_same_thread=False,
                timeout=30,
                isolation_level=None,
            )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout=30000")
        if root is not None:
            _enable_wal(self._connection, directory / "eligibility.sqlite3")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS repair_eligibility (
                claim_key TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                biztype TEXT NOT NULL,
                carrier TEXT NOT NULL,
                problem_key TEXT NOT NULL,
                claimed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )
            """
        )
        self._migrate_legacy_json()

    def claim(self, run_id: str, biztype: str, carrier: str, problem_key: str) -> bool:
        """原子领取资格；只有第一次插入成功返回 True。"""
        key = f"{run_id}|{biztype}|{carrier}"
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                cursor = self._connection.execute(
                    """
                    INSERT OR IGNORE INTO repair_eligibility
                        (claim_key, run_id, biztype, carrier, problem_key)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (key, run_id, biztype, carrier, problem_key),
                )
                claimed = cursor.rowcount == 1
                self._connection.execute("COMMIT")
                return claimed
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def claimed(self) -> dict[str, str]:
        """返回登记快照，兼容原有调用约定。"""
        with self._lock:
            rows = self._connection.execute(
                "SELECT claim_key, problem_key FROM repair_eligibility ORDER BY claimed_at, claim_key"
            ).fetchall()
            return {str(row["claim_key"]): str(row["problem_key"]) for row in rows}

    def clear(self) -> None:
        """清空登记；供明确调用的测试夹具使用。"""
        with self._lock:
            self._connection.execute("DELETE FROM repair_eligibility")

    def close(self) -> None:
        """释放 SQLite 连接。"""
        with self._lock:
            self._connection.close()

    def _migrate_legacy_json(self) -> None:
        path = self._legacy_path
        if path is None or not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # 保留损坏的旧文件供运维检查，不静默删除或覆盖。
            return
        if not isinstance(payload, dict):
            return
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                for key, problem_key in payload.items():
                    parts = str(key).split("|", 2)
                    if len(parts) != 3:
                        continue
                    self._connection.execute(
                        """
                        INSERT OR IGNORE INTO repair_eligibility
                            (claim_key, run_id, biztype, carrier, problem_key)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (str(key), parts[0], parts[1], parts[2], str(problem_key)),
                    )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
