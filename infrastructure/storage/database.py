"""每轮一个 SQLite 文件，集中保存小状态、指标与不可变历史。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from infrastructure.storage.sqlite import open_sqlite


class RecordDatabase:
    """短事务读写；无数据时不会自动创建数据库。"""

    def __init__(self, run_dir: Path) -> None:
        self.path = run_dir / "records.sqlite"

    def _connect(self) -> sqlite3.Connection:
        connection = open_sqlite(self.path, timeout=30, busy_timeout_ms=30000)
        connection.executescript(
            "CREATE TABLE IF NOT EXISTS state ("
            "scope TEXT NOT NULL, name TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(scope,name));"
            "CREATE TABLE IF NOT EXISTS events ("
            "id INTEGER PRIMARY KEY, scope TEXT NOT NULL, stream TEXT NOT NULL, "
            "identity TEXT, body TEXT NOT NULL, UNIQUE(scope,stream,identity));"
            "CREATE INDEX IF NOT EXISTS events_stream ON events(stream,scope,id);"
        )
        return connection

    @contextmanager
    def worker_lease_guard(self, *, owner_id: str, lease_token: str) -> Iterator[None]:
        """在同一 SQLite 写事务中校验租约并执行受保护写入。

        BEGIN IMMEDIATE 持有数据库写锁直到调用方完成写入，因此新的 worker
        不能在校验与写入之间接管租约。调用方不得在 guard 内再次打开本库写事务。
        """
        from infrastructure.storage.lifecycle import WorkerLeaseLost

        if not owner_id or not lease_token:
            raise WorkerLeaseLost("worker lease identity is incomplete")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT body FROM state WHERE scope='' AND name='lifecycle'"
                ).fetchone()
                state = json.loads(row[0]) if row else {}
                try:
                    expires = datetime.fromisoformat(
                        str(state.get("worker_lease_expires_at") or "")
                    )
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=timezone.utc)
                except ValueError:
                    expires = datetime.min.replace(tzinfo=timezone.utc)
                if (
                    state.get("worker_lease_owner") != owner_id
                    or state.get("worker_lease_token") != lease_token
                    or expires <= datetime.now(timezone.utc)
                ):
                    raise WorkerLeaseLost("worker lease lost before persistent write")
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def update_with_worker_lease(
        self, scope: str, name: str, *, owner_id: str, lease_token: str,
        transform: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        """校验 Run 租约并在同一 SQLite 事务内更新状态行。"""
        from infrastructure.storage.lifecycle import WorkerLeaseLost

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                lease_row = connection.execute(
                    "SELECT body FROM state WHERE scope='' AND name='lifecycle'"
                ).fetchone()
                lease = json.loads(lease_row[0]) if lease_row else {}
                try:
                    expires = datetime.fromisoformat(
                        str(lease.get("worker_lease_expires_at") or "")
                    )
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=timezone.utc)
                except ValueError:
                    expires = datetime.min.replace(tzinfo=timezone.utc)
                if (
                    lease.get("worker_lease_owner") != owner_id
                    or lease.get("worker_lease_token") != lease_token
                    or expires <= datetime.now(timezone.utc)
                ):
                    raise WorkerLeaseLost("worker lease lost before lifecycle update")
                row = connection.execute(
                    "SELECT body FROM state WHERE scope=? AND name=?", (scope, name),
                ).fetchone()
                value = transform(json.loads(row[0]) if row else {})
                connection.execute(
                    "INSERT INTO state(scope,name,body) VALUES(?,?,?) "
                    "ON CONFLICT(scope,name) DO UPDATE SET body=excluded.body",
                    (scope, name, json.dumps(value, ensure_ascii=False, default=str)),
                )
                connection.commit()
                return value
            except BaseException:
                connection.rollback()
                raise

    def get(self, scope: str, name: str) -> dict[str, Any] | None:
        """读取状态；不存在时返回空。"""
        if not self.path.is_file():
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT body FROM state WHERE scope=? AND name=?", (scope, name),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def update(
        self, scope: str, name: str, transform: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        """事务内读改写，避免心跳与控制请求互相覆盖。"""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT body FROM state WHERE scope=? AND name=?", (scope, name),
            ).fetchone()
            value = transform(json.loads(row[0]) if row else {})
            connection.execute(
                "INSERT INTO state(scope,name,body) VALUES(?,?,?) "
                "ON CONFLICT(scope,name) DO UPDATE SET body=excluded.body",
                (scope, name, json.dumps(value, ensure_ascii=False, default=str)),
            )
        return value

    def states(self, name: str) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT body FROM state WHERE name=? ORDER BY scope", (name,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def append(
        self, scope: str, stream: str, value: Any, *,
        identity: str | None = None, connection: sqlite3.Connection | None = None,
    ) -> None:
        """追加指标或历史；可复用租约事务连接，避免嵌套写锁。"""
        parameters = (
            scope, stream, identity, json.dumps(value, ensure_ascii=False, default=str),
        )
        if connection is not None:
            connection.execute(
                "INSERT OR IGNORE INTO events(scope,stream,identity,body) VALUES(?,?,?,?)",
                parameters,
            )
            return
        with closing(self._connect()) as owned, owned:
            owned.execute(
                "INSERT OR IGNORE INTO events(scope,stream,identity,body) VALUES(?,?,?,?)",
                parameters,
            )

    def rows(self, stream: str, scope: str | None = None) -> list[dict[str, Any]]:
        """按写入顺序读取流，可读取全轮或指定任务。"""
        if not self.path.is_file():
            return []
        query, parameters = "SELECT body FROM events WHERE stream=?", [stream]
        if scope is not None:
            query += " AND scope=?"
            parameters.append(scope)
        with closing(self._connect()) as connection:
            rows = connection.execute(query + " ORDER BY id", parameters).fetchall()
        return [json.loads(row[0]) for row in rows]


def model_calls(run_dir: Path, task_id: str | None = None) -> list[dict[str, Any]]:
    """读取当前运行的集中模型调用账本。"""
    return RecordDatabase(run_dir).rows("model_calls", task_id)
