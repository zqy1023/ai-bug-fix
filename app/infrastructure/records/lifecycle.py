"""Durable lifecycle projections for run- and task-level status.

Step records are append/update audit facts; lifecycle state is the operational
status used by APIs and the workbench. The two must not be conflated.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.domain.keys import safe_name
from app.infrastructure.records.database import RecordDatabase

_ACTIVE = {"queued", "running", "retrying", "pause_requested", "cancel_requested"}
_TERMINAL = {"succeeded", "partial_failed", "failed", "cancelled", "manual", "skipped", "blocked"}

logger = logging.getLogger(__name__)


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

    def update_run(self, run_id: str, *, allow_reactivate: bool = False, **changes: Any) -> dict[str, Any]:
        return self._update_state(
            run_id, "", {"run_id": run_id, "status": "queued"}, changes,
            allow_reactivate=allow_reactivate,
        )

    def update_task(
        self, run_id: str, task_id: str, *,
        allow_reactivate: bool = False, **changes: Any,
    ) -> dict[str, Any]:
        requested = changes.get("status")
        if (
            not allow_reactivate
            and isinstance(requested, str)
            and requested not in _TERMINAL
        ):
            # Run 仍处于活动态时，任务离开终态是真实重跑（控制面的重试/恢复会
            # 先把 Run 置回活动态）；只有 Run 已收敛后的活动态写入才是迟到回退，
            # 必须在 transform 里拒绝。不能用"调用点是否显式传 allow_reactivate"
            # 判断，否则单步执行、continue_after 与 resume 会被整体锁死。
            run_status = str(self.read_run(run_id).get("status") or "").lower()
            allow_reactivate = run_status in _ACTIVE
        return self._update_state(
            run_id, safe_name(task_id),
            {"run_id": run_id, "task_id": task_id, "status": "pending"}, changes,
            allow_reactivate=allow_reactivate,
        )

    def _database(self, run_id: str) -> RecordDatabase:
        return RecordDatabase(self.root / safe_name(run_id))

    def _update_state(
        self, run_id: str, task_id: str,
        defaults: dict[str, Any], changes: dict[str, Any],
        *, allow_reactivate: bool = False,
    ) -> dict[str, Any]:
        def transform(current: dict[str, Any]) -> dict[str, Any]:
            value = {**defaults, **current}
            updated = {key: item for key, item in changes.items() if item is not _UNSET}
            current_status = value.get("status")
            requested_status = updated.get("status")
            if (
                not allow_reactivate
                and current_status in _TERMINAL
                and isinstance(requested_status, str)
                and requested_status not in _TERMINAL
            ):
                # 终态是已收敛的审计事实：迟到的写入（例如上一轮遗留的
                # running 记录）只能补充其它字段，不能把任务重新拉回活动态。
                # 明确的控制面重试必须显式传 allow_reactivate=True。
                logger.warning(
                    "拒绝终态回退 run_id=%s task_id=%s current=%s requested=%s",
                    run_id, task_id or "<run>", current_status, requested_status,
                )
                for key in ("status", "finished_at", "workspace_released"):
                    updated.pop(key, None)
            value.update(updated)
            now = _now()
            value["updated_at"] = now
            if task_id and updated.get("status") and updated["status"] not in _TERMINAL:
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
