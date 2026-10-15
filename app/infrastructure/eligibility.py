"""跨进程安全的执行轮次修复资格登记。"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Iterator


class ExecutionEligibility:
    """同一 run、biztype、carrier 只允许领取一次资格。"""

    def __init__(self, root: str | Path | None = None) -> None:
        self._lock = Lock()
        self._path = Path(root).resolve() / "eligibility.json" if root else None
        self._claimed: dict[str, str] = {}
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._file_lock():
                self._claimed = self._read()

    def claim(self, run_id: str, biztype: str, carrier: str, problem_key: str) -> bool:
        if not run_id or not biztype or not carrier:
            raise ValueError("领取修复资格必须提供 run_id、biztype 和 carrier")
        key = "|".join((run_id, biztype, carrier))
        with self._lock:
            if self._path is None:
                if key in self._claimed:
                    return False
                self._claimed[key] = problem_key
                return True
            with self._file_lock():
                claimed = self._read()
                if key in claimed:
                    self._claimed = claimed
                    return False
                claimed[key] = problem_key
                self._write(claimed)
                self._claimed = claimed
                return True

    def claimed(self) -> dict[str, str]:
        with self._lock:
            if self._path is None:
                return dict(self._claimed)
            with self._file_lock():
                self._claimed = self._read()
                return dict(self._claimed)

    def clear(self) -> None:
        """仅供测试夹具清理；生产运行不得调用。"""
        with self._lock:
            if self._path is None:
                self._claimed.clear()
                return
            with self._file_lock():
                self._write({})
                self._claimed = {}

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        """使用 OS 文件锁实现跨进程互斥，兼容 Windows 与 POSIX。"""
        assert self._path is not None
        lock_path = self._path.with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as lock_file:
            if os.name == "nt":
                import msvcrt
                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write(b"0")
                    lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _read(self) -> dict[str, str]:
        assert self._path is not None
        if not self._path.exists():
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise RuntimeError(f"修复资格登记文件损坏: {self._path}") from error
        if not isinstance(payload, dict):
            raise RuntimeError(f"修复资格登记文件格式错误: {self._path}")
        return {str(key): str(value) for key, value in payload.items()}

    def _write(self, payload: dict[str, str]) -> None:
        assert self._path is not None
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self._path.name}.", suffix=".tmp", dir=self._path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            temporary.unlink(missing_ok=True)


__all__ = ["ExecutionEligibility"]
