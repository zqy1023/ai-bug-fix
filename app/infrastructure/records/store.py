"""步骤记录仓库。

一次执行一份 ``steps.json``：run 级步骤是顶层 key，任务步骤挂在
``tasks.<task_id>`` 下。大体量业务数据走 ``artifacts/``，记录里只留引用。

**单写者不变量**：``graph.py`` 的任务分发严格串行，一次执行同时只有一个节点在
写，所以 ``save`` 的"读-改-写"放在进程内的锁里就够了。跨进程并发写同一个 run
不被支持——光靠临时文件加替换防不住丢更新，这里不假装能防。

记录只接受当前 schema_version，不转换旧协议。
"""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import time
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.domain.errors import StepFailed
from app.domain.keys import safe_name
from app.domain.models import utc_now
from app.application.audit import SCHEMA_VERSION, ArtifactRef, StepError, StepRecord
from app.infrastructure.records.lifecycle import LifecycleStore
from app.infrastructure.records.database import RecordDatabase
from app.infrastructure.records.policy import artifact_mode
from app.workflow.spec import RecordView
from config.settings import settings

# 单条记录的硬上限：大字段移出后不该再接近 MB，撞上就是"漏了搬字段"。
_JSON_MAX_BYTES = settings.record_json_max_bytes

class RecordStore:
    """读写执行目录下的步骤记录。"""

    def __init__(self, root: str | Path) -> None:
        """指定运行时根目录。"""
        self.root = Path(root).resolve()
        (self.root / "runs").mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def run_dir(self, run_id: str) -> Path:
        """一次执行的目录。"""
        return self.root / "runs" / safe_name(run_id)

    def task_dir(self, run_id: str, task_id: str) -> Path:
        """一个任务的目录。"""
        return self.run_dir(run_id) / "tasks" / safe_name(task_id)

    def steps_path(self, run_id: str) -> Path:
        """一次执行唯一的步骤记录。"""
        return self.run_dir(run_id) / "steps.json"

    def _read_steps(self, run_id: str) -> dict[str, Any]:
        path = self.steps_path(run_id)
        if not path.is_file():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"执行 {run_id} 的 steps.json 必须是对象")
        return payload

    def _write_steps(self, run_id: str, payload: dict[str, Any]) -> None:
        path = self.steps_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as file_obj:
            file_obj.write(text)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        for attempt in range(3):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.05)

    def save(
        self,
        run_id: str,
        record: StepRecord,
        task_id: str = "",
        *,
        input_view: RecordView | None = None,
        output_view: RecordView | None = None,
    ) -> Path:
        """写入步骤记录。run 级步骤是顶层 key，任务步骤写入 tasks 列表项。"""
        body = StepRecord.model_validate(record.public(input_view, output_view)).model_dump(mode="json")
        size = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
        if size > _JSON_MAX_BYTES:
            raise StepFailed(
                f"步骤记录 {record.name} 达 {size} 字节，超过 {_JSON_MAX_BYTES} 字节上限，大字段必须先落 artifact",
                code="ARTIFACT_INVALID_FORMAT",
            )
        with self._lock, _process_lock(self.run_dir(run_id) / ".steps.lock"):
            payload = self._read_steps(run_id)
            if task_id:
                tasks = payload.setdefault("tasks", [])
                if not isinstance(tasks, list):
                    raise StepFailed(
                        f"执行 {run_id} 的 steps.json.tasks 结构损坏，拒绝清空已有任务记录",
                        code="STEP_RECORD_CORRUPTED",
                    )
                # steps.json 是审计投影，历史任务槽位只能增加，不能因旧进程
                # 的快照写回而倒退。若当前写入前刚发生过覆盖，.bak 仍可能
                # 保留更多槽位，先合并恢复，再写入本次步骤。
                backup_path = self.steps_path(run_id).with_name("steps.json.bak")
                if backup_path.is_file():
                    try:
                        backup = json.loads(backup_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        backup = {}
                    backup_tasks = backup.get("tasks") if isinstance(backup, dict) else None
                    if isinstance(backup_tasks, list):
                        known = {
                            str(item.get("task_id"))
                            for item in tasks
                            if isinstance(item, dict) and item.get("task_id")
                        }
                        for item in backup_tasks:
                            if not isinstance(item, dict) or not item.get("task_id"):
                                continue
                            if str(item["task_id"]) not in known:
                                tasks.append(item)
                                known.add(str(item["task_id"]))
                slot = next((item for item in tasks if isinstance(item, dict) and item.get("task_id") == task_id), None)
                if slot is None:
                    slot = {"task_id": task_id}
                    tasks.append(slot)
                previous = slot.get(record.name)
                if isinstance(previous, dict) and previous.get("finished_at") and previous != body:
                    identity = hashlib.sha256(json.dumps(previous, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                    self.database(run_id).append(task_id, "step_history", previous, identity=identity)
                slot[record.name] = body
            else:
                payload[record.name] = body
            path = self.steps_path(run_id)
            if path.is_file():
                try:
                    shutil.copyfile(path, path.with_name("steps.json.bak"))
                except OSError:
                    pass
            self._write_steps(run_id, payload)
        try:
            self._update_lifecycle(run_id, task_id, record)
        except Exception:
            # Audit history is authoritative; lifecycle projection failure must be visible
            # in logs but must not overwrite a successfully persisted step record.
            import logging
            logging.getLogger(__name__).exception(
                "更新运行/任务生命周期失败 run_id=%s task_id=%s step=%s",
                run_id, task_id, record.name,
            )
        return self.steps_path(run_id)

    def _update_lifecycle(self, run_id: str, task_id: str, record: StepRecord) -> None:
        lifecycle = LifecycleStore(self.root)
        phase = record.name
        error = record.error.model_dump(mode="json") if record.error else {}
        if not task_id:
            if record.status == "waiting":
                lifecycle.update_run(
                    run_id, status="paused", phase=phase, current_step=phase,
                    attempt=record.attempt, error=error,
                )
            elif record.status == "failed" or str(record.result or "").upper() in {"FAIL", "FAILED", "REJECTED"}:
                lifecycle.update_run(
                    run_id, status="retrying", phase=phase, current_step=phase,
                    attempt=record.attempt, error=error,
                    last_error_summary=record.summary or "",
                )
            else:
                lifecycle.update_run(
                    run_id, status="running", phase=phase, current_step=phase,
                    attempt=record.attempt, error={},
                )
            return

        run_lifecycle = lifecycle.read_run(run_id)
        if run_lifecycle.get("worker_active") and run_lifecycle.get("status") in {"running", "retrying"}:
            lifecycle.update_run(
                run_id, phase="run_task", current_step=phase, current_task_id=task_id,
            )
        common = {"current_step": phase, "attempt": record.attempt}
        if record.status == "waiting":
            status = "waiting_approval" if phase == "request_approval" else "paused"
            lifecycle.update_task(
                run_id, task_id, status=status, **common,
                terminal_step="", terminal_reason="", next_action="",
            )
        elif phase == "finalize_execution" and record.status != "running":
            output = record.output if isinstance(record.output, dict) else {}
            delivery_status = str(output.get("delivery_status") or record.result or "").upper()
            if delivery_status in {"SUCCESS", "SUCCEEDED", "COMPLETED", "DELIVERED"}:
                status = "succeeded"
            elif delivery_status in {"MANUAL", "INSUFFICIENT_EVIDENCE", "MANUAL_REVIEW_REQUIRED"}:
                status = "manual"
            elif delivery_status == "BLOCKED":
                status = "blocked"
            elif delivery_status == "CANCELLED":
                status = "cancelled"
            elif delivery_status == "SKIPPED":
                status = "skipped"
            elif record.status == "failed" or delivery_status in {"FAILED", "FAIL", "REJECTED"}:
                status = "failed"
            else:
                status = "succeeded" if record.status in {"succeeded", "skipped"} else "failed"
            lifecycle.update_task(
                run_id, task_id, status=status, **common,
                terminal_step=str(output.get("terminal_step") or phase),
                terminal_reason=str(output.get("terminal_reason") or record.summary or ""),
                next_action=str(output.get("next_action") or ""),
                error=error,
            )
        elif record.status == "succeeded" and str(record.result or "").upper() in {"FAIL", "FAILED", "REJECTED"}:
            # Business FAIL/REJECTED is an executed step, not an exception, but its
            # router may still send the task through retry_prepare.
            reason = record.summary or f"{phase} returned {record.result}"
            lifecycle.update_task(
                run_id, task_id, status="retrying", **common,
                last_error={"code": "BUSINESS_STEP_FAILED", "message": reason[:1200]},
                last_error_summary=reason[:1200],
            )
        elif record.status == "failed":
            # The graph may still route this failure through retry_prepare; don't
            # prematurely mark the whole task failed before that route completes.
            lifecycle.update_task(
                run_id, task_id, status="retrying", **common,
                last_error=error, last_error_summary=record.summary or "",
            )
        else:
            # A newly running/succeeded step proves that this task is active again,
            # even if an earlier attempt left a failed step record.
            lifecycle.update_task(
                run_id, task_id, status="running", **common,
                error={}, last_error=error if record.status == "failed" else {},
            )

    def complete_task(self, run_id: str, task_id: str) -> None:
        """在任务子图退出时依据最终报告收敛，避免交付将终态重新标为 running。"""
        record = self.load(run_id, "finalize_execution", task_id)
        if record is None:
            raise StepFailed("任务子图退出但缺少终态步骤记录")
        from app.workflow.steps.finalize_execution.node import refresh_completed_report
        refresh_completed_report(self, run_id, task_id)
        self._update_lifecycle(run_id, task_id, record)
        if str(record.output.get("delivery_status") or "").upper() == "BLOCKED":
            LifecycleStore(self.root).update_task(run_id, task_id, workspace_released=False)
            return
        LifecycleStore(self.root).update_task(run_id, task_id, workspace_released=True)
        from app.infrastructure.filesystem.workspace_gc import collect_configured_workspaces
        from app.infrastructure.config import load_config
        from dataclasses import replace
        collect_configured_workspaces(replace(load_config(), runtime_dir=self.root))

    def report_progress(self, run_id: str, step: str, message: str, counts: dict[str, int]) -> None:
        """记录不含原始日志的处理进度，供轮询和事件流展示。"""
        LifecycleStore(self.root).update_run(
            run_id, progress={"step": step, "message": message, "counts": counts},
            progress_at=utc_now(),
        )

    def load(self, run_id: str, step_name: str, task_id: str = "") -> StepRecord | None:
        """读取步骤记录；不存在时返回空；旧协议直接拒绝。"""
        with self._lock:
            payload = self._read_steps(run_id)
        body = _step_body(payload, step_name, task_id)
        if body is None and task_id:
            from app.workflow.definitions import REVIEW_EVIDENCE_STEPS
            if step_name in REVIEW_EVIDENCE_STEPS:
                relative = f"artifacts/review_agent/steps/{step_name}.json"
                if (self.task_dir(run_id, task_id) / relative).is_file():
                    body = self.read_artifact(run_id, relative, task_id)
        if body is None:
            return None
        return _validated(body, run_id, step_name)

    def list_steps(self, run_id: str, task_id: str = "") -> list[StepRecord]:
        """返回已落盘的步骤记录。任务级只返回该任务，run 级不含 tasks。"""
        with self._lock:
            payload = self._read_steps(run_id)
        if task_id:
            tasks = payload.get("tasks")
            slot = next(
                (item for item in tasks if isinstance(item, dict) and item.get("task_id") == task_id),
                None,
            ) if isinstance(tasks, list) else None
            source = slot or {}
        else:
            source = {key: value for key, value in payload.items() if key != "tasks"}
        records: list[StepRecord] = []
        for value in source.values():
            if isinstance(value, dict) and isinstance(value.get("name"), str):
                records.append(_validated(value, run_id, str(value.get("name") or "")))
        return records

    def _base(self, run_id: str, task_id: str) -> Path:
        return self.task_dir(run_id, task_id) if task_id else self.run_dir(run_id)

    def _target(self, run_id: str, relative: str, task_id: str) -> tuple[Path, Path]:
        """把相对路径解析到所属根目录内。越界、绝对路径、符号链接外逃都拒绝。"""
        if not relative or Path(relative).is_absolute():
            raise StepFailed(f"产物路径必须是相对路径: {relative!r}", code="ARTIFACT_PATH_INVALID")
        base = self._base(run_id, task_id)
        target = base / relative
        root = base.resolve()
        parent = target.parent.resolve()
        if not parent.is_relative_to(root) or not target.resolve().is_relative_to(root):
            raise StepFailed(f"产物路径越界: {relative!r}", code="ARTIFACT_PATH_INVALID")
        return base, target

    def write_artifact(self, run_id: str, relative: str, value: Any, task_id: str = "") -> str:
        """写大产物，返回相对任务/执行目录的路径。不进入 steps.json。"""
        _, target = self._target(run_id, relative, task_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        # 建目录之后再确认一次：父目录可能本身就是指向根目录之外符号链接。
        base = self._base(run_id, task_id)
        if not target.parent.resolve().is_relative_to(base.resolve()):
            raise StepFailed(f"产物目录越界: {relative!r}", code="ARTIFACT_PATH_INVALID")
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
        if task_id and target.is_file() and relative.startswith(("agents/", "artifacts/", "reports/")):
            previous = target.read_bytes()
            if previous != text.encode("utf-8"):
                identity = hashlib.sha256(previous).hexdigest()
                try:
                    previous_value = json.loads(previous)
                except (ValueError, UnicodeDecodeError):
                    previous_value = {"raw": previous.decode("utf-8", errors="replace")}
                self.database(run_id).append(
                    task_id, "artifact_history", {"path": relative, "value": previous_value},
                    identity=f"{relative}:{identity}",
                )
        # 临时文件与目标同目录：同文件系统才能原子替换，写完整才返回引用。
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(target)
        return target.relative_to(self._base(run_id, task_id)).as_posix()

    def read_artifact(self, run_id: str, relative: str, task_id: str = "") -> Any:
        """读回已落盘产物。``steps.json`` 里只留引用，全文仍在这里。"""
        base, target = self._target(run_id, relative, task_id)
        if relative == "metrics/agent.jsonl":
            return self.read_metrics(run_id, task_id)
        if not target.is_file():
            raise StepFailed(f"产物不存在: {relative}", code="ARTIFACT_NOT_FOUND")
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise StepFailed(f"产物不是合法 JSON: {relative}", code="ARTIFACT_INVALID_FORMAT") from error

    def read_ref(self, run_id: str, ref: ArtifactRef, task_id: str = "") -> Any:
        """按引用读产物。**唯一的引用解析入口**，调用点不许自己拼路径。

        run 级引用（``ref.task_id`` 为空）本次执行内任意任务可读；任务级引用只有
        该任务能读。
        """
        if ref.task_id and ref.task_id != task_id:
            raise StepFailed(
                f"产物 {ref.name} 属于任务 {ref.task_id}，当前任务 {task_id or 'run'} 无权读取",
                code="ARTIFACT_PATH_INVALID",
            )
        return self.read_artifact(run_id, ref.path, ref.task_id)

    def append_artifact(self, run_id: str, relative: str, value: Any, task_id: str = "") -> str:
        """追加 JSONL 运行指标，不覆盖同一任务的历史调用。"""
        base, target = self._target(run_id, relative, task_id)
        if relative == "metrics/agent.jsonl":
            self.database(run_id).append(task_id, "agent_metrics", value)
            return relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.parent.resolve().is_relative_to(base.resolve()):
            raise StepFailed(f"产物目录越界: {relative!r}", code="ARTIFACT_PATH_INVALID")
        with target.open("a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        return target.relative_to(self._base(run_id, task_id)).as_posix()

    def database(self, run_id: str) -> RecordDatabase:
        """本轮小记录的集中存储。"""
        return RecordDatabase(self.run_dir(run_id))

    def read_metrics(self, run_id: str, task_id: str) -> list[dict[str, Any]]:
        """读取当前运行的集中指标。"""
        return self.database(run_id).rows("agent_metrics", task_id)

    def step_history(self, run_id: str, task_id: str) -> list[dict[str, Any]]:
        """读取当前协议保存的步骤历史。"""
        return self.database(run_id).rows("step_history", task_id)

    def create_run(self, run_id: str, payload: dict[str, Any]) -> None:
        """创建不可覆盖的执行元数据，供真实单步重放使用。"""
        directory = self.run_dir(run_id)
        directory.mkdir(parents=True, exist_ok=True)
        body = {**payload, "artifact_mode": artifact_mode(), "updated_at": utc_now()}
        path = directory / "run.json"
        with path.open("x", encoding="utf-8") as file_obj:
            json.dump(body, file_obj, ensure_ascii=False, indent=2)

        LifecycleStore(self.root).update_run(
            run_id, status="queued", phase="queued", current_step="",
            started_at=None, finished_at=None, error={}, terminal_reason="",
        )

    def load_run(self, run_id: str) -> dict[str, Any]:
        """读取执行元数据；缺失或损坏时拒绝使用当前配置冒充原始输入。"""
        path = self.run_dir(run_id) / "run.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"执行 {run_id} 缺少有效 run.json，不能重建真实输入") from error
        if not isinstance(payload, dict):
            raise ValueError(f"执行 {run_id} 的 run.json 必须是对象")
        return payload


def _validated(body: dict[str, Any], run_id: str, step_name: str) -> StepRecord:
    """校验一条记录，报错时带上执行与步骤名，便于定位。"""
    try:
        if body.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("步骤记录协议版本不受支持")
        return StepRecord.model_validate(body)
    except Exception as error:
        raise ValueError(f"执行 {run_id} 的步骤记录 {step_name or '<未知>'} 无法读取: {error}") from error


def _step_body(payload: dict[str, Any], step_name: str, task_id: str) -> dict[str, Any] | None:
    if task_id:
        tasks = payload.get("tasks")
        if not isinstance(tasks, list):
            return None
        slot = next((item for item in tasks if isinstance(item, dict) and item.get("task_id") == task_id), None)
        body = slot.get(step_name) if isinstance(slot, dict) else None
    else:
        body = payload.get(step_name)
    return body if isinstance(body, dict) else None


@contextmanager
def _process_lock(path: Path):
    """跨进程保护 steps.json 的读改写；线程锁仍由 RecordStore 自己持有。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                except OSError as error:
                    raise OSError("另一进程持有记录锁") from error
                handle.write("0")
                handle.flush()
            else:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                except OSError as error:
                    raise OSError("另一进程持有记录锁") from error
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


__all__ = ["RecordStore", "StepError"]
