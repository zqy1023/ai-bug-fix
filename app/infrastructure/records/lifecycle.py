"""Durable lifecycle projections for run- and task-level status.

Step records are append/update audit facts; lifecycle state is the operational
status used by APIs and the workbench. The two must not be conflated.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.domain.keys import safe_name
from app.infrastructure.records.database import RecordDatabase

_ACTIVE = {"queued", "running", "retrying", "pause_requested", "cancel_requested"}
_TERMINAL = {"succeeded", "partial_failed", "failed", "cancelled", "manual", "skipped", "blocked"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LifecycleStore:
    """仅从当前运行的 SQLite 读取和更新生命周期。"""

    def __init__(self, runtime_dir: str | Path) -> None:
        self.root = Path(runtime_dir).resolve() / "runs"

    def read_run(self, run_id: str) -> dict[str, Any]:
        return self._database(run_id).get("", "lifecycle") or {}

    def read_task(self, run_id: str, task_id: str) -> dict[str, Any]:
        return self._database(run_id).get(safe_name(task_id), "lifecycle") or {}

    def update_run(self, run_id: str, **changes: Any) -> dict[str, Any]:
        return self._update_state(run_id, "", {"run_id": run_id, "status": "queued"}, changes)

    def update_task(self, run_id: str, task_id: str, **changes: Any) -> dict[str, Any]:
        return self._update_state(run_id, safe_name(task_id), {"run_id": run_id, "task_id": task_id, "status": "pending"}, changes)

    def _database(self, run_id: str) -> RecordDatabase:
        return RecordDatabase(self.root / safe_name(run_id))

    def _update_state(
        self, run_id: str, task_id: str,
        defaults: dict[str, Any], changes: dict[str, Any],
    ) -> dict[str, Any]:
        def transform(current: dict[str, Any]) -> dict[str, Any]:
            value = {**defaults, **current}
            value.update({key: item for key, item in changes.items() if item is not _UNSET})
            now = _now()
            value["updated_at"] = now
            if task_id and changes.get("status") and changes["status"] not in _TERMINAL:
                value["workspace_released"] = False
            if value.get("status") in _TERMINAL and not value.get("finished_at"):
                value["finished_at"] = now
            elif value.get("status") not in _TERMINAL:
                value["finished_at"] = None
                if value.get("status") in _ACTIVE:
                    if not value.get("started_at"):
                        value["started_at"] = now
                    value["heartbeat_at"] = now
            return value

        return self._database(run_id).update(
            task_id, "lifecycle", transform,
        )


class _Unset:
    pass


_UNSET = _Unset()


__all__ = ["LifecycleStore"]
