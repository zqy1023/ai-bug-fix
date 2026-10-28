"""工作台幂等控制、停机控制与审计持久化。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config.settings import settings
from server.context import _config

def _control_db():
    """Durable idempotency, stop-request and audit metadata, separate from workflow artifacts."""
    import sqlite3
    config = _config()
    runtime_dir = config.runtime_dir
    runtime_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        str(runtime_dir / "workbench-control.sqlite3"),
        timeout=config.workbench_sqlite_timeout_seconds,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(f"PRAGMA busy_timeout={config.workbench_sqlite_busy_timeout_ms}")
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
    return connection

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
