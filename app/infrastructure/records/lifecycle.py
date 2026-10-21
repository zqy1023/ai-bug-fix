"""Durable lifecycle projections for run- and task-level status.

Step records are append/update audit facts; lifecycle state is the operational
status used by APIs and the workbench. The two must not be conflated.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.domain.keys import safe_name

_ACTIVE = {"queued", "running", "retrying", "pause_requested", "cancel_requested"}
_TERMINAL = {"succeeded", "partial_failed", "failed", "cancelled", "manual"}
_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


class LifecycleStore:
    """Small atomic JSON state store; step history remains in steps.json."""

    def __init__(self, runtime_dir: str | Path) -> None:
        self.root = Path(runtime_dir).resolve() / "runs"

    def run_path(self, run_id: str) -> Path:
        return self.root / safe_name(run_id) / "lifecycle.json"

    def task_path(self, run_id: str, task_id: str) -> Path:
        return self.root / safe_name(run_id) / "tasks" / safe_name(task_id) / "lifecycle.json"

    def read_run(self, run_id: str) -> dict[str, Any]:
        return self._read(self.run_path(run_id))

    def read_task(self, run_id: str, task_id: str) -> dict[str, Any]:
        return self._read(self.task_path(run_id, task_id))

    def update_run(self, run_id: str, **changes: Any) -> dict[str, Any]:
        path = self.run_path(run_id)
        return self._update(path, {"run_id": run_id, "status": "queued"}, changes)

    def update_task(self, run_id: str, task_id: str, **changes: Any) -> dict[str, Any]:
        path = self.task_path(run_id, task_id)
        return self._update(
            path,
            {"run_id": run_id, "task_id": task_id, "status": "pending"},
            changes,
        )

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @classmethod
    def _update(
        cls,
        path: Path,
        defaults: dict[str, Any],
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _path_lock(path):
            current = cls._read(path)
            value = {**defaults, **current}
            value.update({key: item for key, item in changes.items() if item is not _UNSET})
            now = _now()
            value["updated_at"] = now
            if value.get("status") in _TERMINAL and not value.get("finished_at"):
                value["finished_at"] = now
            elif value.get("status") in _ACTIVE:
                value["finished_at"] = None
                value.setdefault("started_at", now)
                value["heartbeat_at"] = now
            temporary = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            for attempt in range(3):
                try:
                    temporary.replace(path)
                    break
                except PermissionError:
                    if attempt == 2:
                        raise
                    time.sleep(0.05)
            return value


class _Unset:
    pass


_UNSET = _Unset()


__all__ = ["LifecycleStore"]
