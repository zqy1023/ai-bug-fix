"""每轮一个 SQLite 文件，集中保存小状态、指标与不可变历史。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Callable


class RecordDatabase:
    """短事务读写；读取旧运行时不会自动创建数据库。"""

    def __init__(self, run_dir: Path) -> None:
        self.path = run_dir / "records.sqlite"

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA busy_timeout=30000")
        connection.executescript(
            "CREATE TABLE IF NOT EXISTS state ("
            "scope TEXT NOT NULL, name TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(scope,name));"
            "CREATE TABLE IF NOT EXISTS events ("
            "id INTEGER PRIMARY KEY, scope TEXT NOT NULL, stream TEXT NOT NULL, "
            "identity TEXT, body TEXT NOT NULL, UNIQUE(scope,stream,identity));"
            "CREATE INDEX IF NOT EXISTS events_stream ON events(stream,scope,id);"
        )
        return connection

    def get(self, scope: str, name: str) -> dict[str, Any] | None:
        """读取状态；不存在时由调用方回退旧文件。"""
        if not self.path.is_file():
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT body FROM state WHERE scope=? AND name=?", (scope, name),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def update(
        self, scope: str, name: str, transform: Callable[[dict[str, Any]], dict[str, Any]],
        *, fallback: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """事务内读改写，避免心跳与控制请求互相覆盖。"""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT body FROM state WHERE scope=? AND name=?", (scope, name),
            ).fetchone()
            value = transform(json.loads(row[0]) if row else dict(fallback or {}))
            connection.execute(
                "INSERT INTO state(scope,name,body) VALUES(?,?,?) "
                "ON CONFLICT(scope,name) DO UPDATE SET body=excluded.body",
                (scope, name, json.dumps(value, ensure_ascii=False, default=str)),
            )
        return value

    def append(self, scope: str, stream: str, value: Any, *, identity: str | None = None) -> None:
        """追加指标或历史；历史按内容哈希去重。"""
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT OR IGNORE INTO events(scope,stream,identity,body) VALUES(?,?,?,?)",
                (scope, stream, identity, json.dumps(value, ensure_ascii=False, default=str)),
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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """兼容旧 JSONL，跳过中断写入的残行。"""
    if not path.is_file():
        return []
    values: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                values.append(value)
    return values


def model_calls(run_dir: Path, task_id: str | None = None) -> list[dict[str, Any]]:
    """合并旧调用账本与新集中存储，供工作台读取。"""
    items = read_jsonl(run_dir / "model_calls.jsonl")
    items.extend(RecordDatabase(run_dir).rows("model_calls", task_id))
    if task_id:
        items = [item for item in items if item.get("task_id") == task_id]
    return items
