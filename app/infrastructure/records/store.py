"""步骤记录仓库。

一次执行一份 ``steps.json``：run 级步骤是顶层 key，任务步骤挂在
``tasks.<task_id>`` 下。大体量业务数据走 ``artifacts/``，记录里只留引用。

**单写者不变量**：``graph.py`` 的任务分发严格串行，一次执行同时只有一个节点在
写，所以 ``save`` 的"读-改-写"放在进程内的锁里就够了。跨进程并发写同一个 run
不被支持——光靠临时文件加替换防不住丢更新，这里不假装能防。

**旧记录兼容**：读取边界把没有 ``schema_version`` 的记录按 v0 转换成当前格式，
写入一律只写当前格式。历史 run 不需要批量迁移。
"""

from __future__ import annotations

import json
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
from app.workflow.spec import RecordView

# 单条记录的硬上限：大字段移出后不该再接近 MB，撞上就是"漏了搬字段"。
_JSON_MAX_BYTES = 1 * 1024 * 1024

# v0 → v1 的字段改名与取值映射。
_V0_STATUS = {"passed": "succeeded"}
_V0_RENAMED = {"elapsed_ms": "duration_ms"}


def _error_is_empty(value: Any) -> bool:
    return value is None or (
        isinstance(value, dict) and not str(value.get("code") or "") and not str(value.get("message") or "")
    )


def upgrade_record(body: dict[str, Any]) -> dict[str, Any]:
    """把旧格式记录转换成当前格式。``load`` 与 ``list_steps`` 共用这一处。"""
    if not isinstance(body, dict):
        raise ValueError("步骤记录必须是对象")
    if body.get("schema_version") == SCHEMA_VERSION:
        return body
    converted = {key: value for key, value in body.items() if key not in _V0_RENAMED}
    for old, new in _V0_RENAMED.items():
        if old in body and new not in converted:
            converted[new] = body[old]
    status = str(converted.get("status") or "pending")
    converted["status"] = _V0_STATUS.get(status, status)
    # 旧记录成功时也写空错误对象，新契约里成功就是没有错误对象。
    if str(converted.get("status") or "") != "failed" or _error_is_empty(converted.get("error")):
        converted["error"] = None
    elif isinstance(converted.get("error"), dict):
        converted["error"] = {
            "code": "STEP_EXECUTION_FAILED",
            "message": str(converted["error"].get("message") or ""),
            "retryable": False,
        }
    converted.setdefault("result", None)
    converted.setdefault("summary", "")
    converted.setdefault("artifacts", [])
    converted["schema_version"] = SCHEMA_VERSION
    return converted


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
                f"步骤记录 {record.name} 达 {size} 字节，超过 1MB，大字段必须先落 artifact",
                code="ARTIFACT_INVALID_FORMAT",
            )
        with self._lock, _process_lock(self.run_dir(run_id) / ".steps.lock"):
            payload = self._read_steps(run_id)
            if task_id:
                tasks = payload.setdefault("tasks", [])
                if not isinstance(tasks, list):
                    tasks = []
                    payload["tasks"] = tasks
                slot = next((item for item in tasks if isinstance(item, dict) and item.get("task_id") == task_id), None)
                if slot is None:
                    slot = {"task_id": task_id}
                    tasks.append(slot)
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
        return self.steps_path(run_id)

    def load(self, run_id: str, step_name: str, task_id: str = "") -> StepRecord | None:
        """读取步骤记录；不存在时返回空。旧格式在读取边界转换。"""
        with self._lock:
            payload = self._read_steps(run_id)
        body = _step_body(payload, step_name, task_id)
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
        # 临时文件与目标同目录：同文件系统才能原子替换，写完整才返回引用。
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(target)
        return target.relative_to(self._base(run_id, task_id)).as_posix()

    def read_artifact(self, run_id: str, relative: str, task_id: str = "") -> Any:
        """读回已落盘产物。``steps.json`` 里只留引用，全文仍在这里。"""
        base, target = self._target(run_id, relative, task_id)
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
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.parent.resolve().is_relative_to(base.resolve()):
            raise StepFailed(f"产物目录越界: {relative!r}", code="ARTIFACT_PATH_INVALID")
        with target.open("a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        return target.relative_to(self._base(run_id, task_id)).as_posix()

    def create_run(self, run_id: str, payload: dict[str, Any]) -> None:
        """创建不可覆盖的执行元数据，供真实单步重放使用。"""
        directory = self.run_dir(run_id)
        directory.mkdir(parents=True, exist_ok=True)
        body = {**payload, "updated_at": utc_now()}
        path = directory / "run.json"
        with path.open("x", encoding="utf-8") as file_obj:
            json.dump(body, file_obj, ensure_ascii=False, indent=2)

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
        return StepRecord.model_validate(upgrade_record(body))
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


__all__ = ["RecordStore", "StepError", "upgrade_record"]
