"""跨 Checkpoint、文件和队列的可恢复重试意图，显式记录提交边界。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from typing import Any

from infrastructure.storage.atomic import atomic_write_text
from infrastructure.storage.lifecycle import LifecycleStore


class RetryOperation:
    def __init__(self, store: Any, run_id: str, task_id: str = "", *, kind: str = "retry") -> None:
        self.store, self.run_id, self.task_id = store, run_id, task_id
        self.database = store.database(run_id)
        current = self.database.get("", "retry_intent") or {}
        if current.get("phase") in {"preparing", "committing", "committed", "queued", "recovery_required"}:
            raise RuntimeError("已有未完成重试意图，请先恢复或检查 retry_intent")
        identity = uuid4().hex
        lifecycle = LifecycleStore(store.root)
        reports = [store.run_dir(run_id) / "run.json", store.steps_path(run_id), *list((store.run_dir(run_id) / "reports").glob("*.json"))]
        reports += ([store.task_dir(run_id, task_id) / "reports/final.json"] if task_id else
                    list((store.run_dir(run_id) / "tasks").glob("*/reports/final.json")))
        backups = []
        for index, path in enumerate(reports):
            if not path.is_file():
                continue
            relative = f"retry/operations/{identity}/report-{index}.json"
            store.write_artifact(run_id, relative, {"path": str(path), "text": path.read_text(encoding="utf-8")})
            backups.append(relative)
        self.value = {"operation_id": identity, "kind": kind, "task_id": task_id, "phase": "preparing",
                      "created_at": datetime.now(timezone.utc).isoformat(), "backups": backups,
                      "run_lifecycle": lifecycle.read_run(run_id),
                      "task_lifecycle": lifecycle.read_task(run_id, task_id) if task_id else {}, "moves": []}
        from infrastructure.storage.execution_scope import execution_scope
        with execution_scope(store.root) as connection:
            row = connection.execute("SELECT pause_requested,cancel_requested,updated_at FROM run_control WHERE run_id=?", (run_id,)).fetchone()
            self.value["control_flags"] = list(row) if row else None
        self.save()

    def save(self, phase: str | None = None, **changes: Any) -> None:
        if phase:
            self.value["phase"] = phase
        self.value.update(changes)
        lease = self.store._worker_lease_for(self.run_id)
        if lease:
            self.database.update_with_worker_lease("", "retry_intent", owner_id=lease[0], lease_token=lease[1], transform=lambda _: dict(self.value))
        else:
            self.database.update("", "retry_intent", lambda _: dict(self.value))

    def moving(self, source: Path, target: Path) -> None:
        self.value["moves"].append({"source": str(source), "target": str(target)})
        self.save()

    def rollback(self) -> None:
        if self.value["phase"] not in {"preparing", "rolled_back"}:
            self.save("recovery_required")
            return
        try:
            with self.store._write_guard(self.run_id):
                for relative in self.value["backups"]:
                    saved = self.store.read_artifact(self.run_id, relative)
                    target = Path(saved["path"]).resolve()
                    if not target.is_relative_to(self.store.run_dir(self.run_id).resolve()):
                        raise ValueError("重试报告恢复路径越界")
                    atomic_write_text(target, saved["text"])
                for move in reversed(self.value["moves"]):
                    source, target = Path(move["source"]), Path(move["target"])
                    if target.exists() and not source.exists():
                        target.replace(source)
            self.database.update("", "lifecycle", lambda current: {
                **self.value["run_lifecycle"], **{key: value for key, value in current.items()
                    if key.startswith("worker_lease_") or key in {"worker_active", "heartbeat_at"}}})
            if self.task_id:
                self.database.update(self.task_id, "lifecycle", lambda _: dict(self.value["task_lifecycle"]))
            from infrastructure.storage.execution_scope import execution_scope
            with execution_scope(self.store.root) as connection:
                flags = self.value.get("control_flags")
                if flags:
                    connection.execute("INSERT INTO run_control VALUES(?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET "
                                       "pause_requested=excluded.pause_requested,cancel_requested=excluded.cancel_requested,updated_at=excluded.updated_at",
                                       (self.run_id, *flags))
                else:
                    connection.execute("DELETE FROM run_control WHERE run_id=?", (self.run_id,))
            self.save("rolled_back")
        except BaseException as error:
            self.save("recovery_required", recovery_error=type(error).__name__)
            raise

    def checkpoint(self, graph: Any, config: dict[str, Any], patch: dict[str, Any], *, as_node: str) -> None:
        identity = self.value["operation_id"]
        patch = {**patch, "retry_operation_id": identity}
        path = f"retry/operations/{identity}/checkpoint-patch.json"
        self.store.write_artifact(self.run_id, path, {"patch": patch, "as_node": as_node})
        self.save("committing", checkpoint_patch=path)
        try:
            with self.store._write_guard(self.run_id):
                graph.update_state(config, patch, as_node=as_node)
        except BaseException:
            # update_state 可能已提交但回执丢失，不盲目回滚文件或再追加一次任务。
            try:
                values = graph.get_state(config).values
                committed = isinstance(values, dict) and values.get("retry_operation_id") == identity
            except Exception:
                self.save("recovery_required")
                raise
            self.save("committed" if committed else "preparing")
            raise
        self.save("committed")


def recover_retry(store: Any, run_id: str, graph: Any, config: dict[str, Any]) -> None:
    """恢复已提交或提交回执不明的意图；op id 避免重复追加任务。"""
    database = store.database(run_id)
    intent = database.get("", "retry_intent") or {}
    if intent.get("phase") == "preparing":
        operation = RetryOperation.__new__(RetryOperation)
        operation.store, operation.run_id, operation.task_id = store, run_id, intent.get("task_id", "")
        operation.database, operation.value = database, intent
        operation.rollback()
        return
    if intent.get("phase") not in {"committing", "committed", "recovery_required"}:
        return
    path = intent.get("checkpoint_patch")
    if path:
        saved = store.read_artifact(run_id, path)
        values = graph.get_state(config).values
        if not isinstance(values, dict) or values.get("retry_operation_id") != intent["operation_id"]:
            graph.update_state(config, saved["patch"], as_node=saved["as_node"])
    database.update("", "retry_intent", lambda current: {**current, "phase": "queued"})


def finish_retry(store: Any, run_id: str, worker_lease: tuple[str, str] | None = None) -> None:
    database = store.database(run_id)
    intent = database.get("", "retry_intent") or {}
    if intent and intent.get("phase") in {"queued", "committed"}:
        transform = lambda current: {**current, "phase": "finished"} if current.get("operation_id") == intent["operation_id"] else current
        if worker_lease:
            database.update_with_worker_lease("", "retry_intent", owner_id=worker_lease[0], lease_token=worker_lease[1], transform=transform)
        else:
            database.update("", "retry_intent", transform)
