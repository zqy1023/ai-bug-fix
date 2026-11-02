"""工作台幂等控制、停机控制与审计持久化。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from infrastructure.storage.sqlite import open_sqlite
from server.context import _config


def _control_db():
    """Durable idempotency, stop-request and audit metadata, separate from workflow artifacts."""
    config = _config()
    runtime_dir = config.runtime_dir
    connection = open_sqlite(
        runtime_dir / "workbench-control.sqlite3",
        timeout=config.workbench_sqlite_timeout_seconds,
        busy_timeout_ms=config.workbench_sqlite_busy_timeout_ms,
        row_factory=sqlite3.Row,
        journal_mode="WAL",
    )
    _ensure_control_schema(connection)
    return connection


def _ensure_control_schema(connection) -> None:
    """控制库 Schema 的唯一初始化入口。"""
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            key TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, run_id TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS run_control (
            run_id TEXT PRIMARY KEY, pause_requested INTEGER NOT NULL DEFAULT 0,
            cancel_requested INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS control_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, task_id TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL, actor TEXT NOT NULL DEFAULT 'unknown', reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
    """)
    connection.commit()


def _audit_control(run_id: str, task_id: str, action: str, actor: str, reason: str = "") -> None:
    connection = _control_db()
    try:
        connection.execute(
            "INSERT INTO control_audit(run_id,task_id,action,actor,reason,created_at) VALUES(?,?,?,?,?,?)",
            (run_id, task_id, action, actor[:160], reason[:1000], datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
    finally:
        connection.close()
