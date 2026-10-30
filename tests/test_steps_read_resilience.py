"""steps.json 并发读写的共享冲突回归。

任务板在步骤写入的同时读取 steps.json，在 Windows 上会因 ``os.replace`` 的
delete-pending 窗口随机拿到 ``PermissionError: [Errno 13]``，把整个请求打成 500。
"""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from core.contracts import StepRecord
from infrastructure.storage.store import RecordStore


def _flaky_read_text(target: Path, failures: int | None):
    """让 ``target`` 的前 ``failures`` 次读取抛共享冲突；None 表示一直失败。"""
    real = Path.read_text
    calls = {"count": 0}

    def fake(self, *args, **kwargs):
        if self == target:
            calls["count"] += 1
            if failures is None or calls["count"] <= failures:
                raise PermissionError(13, "Permission denied", str(self))
        return real(self, *args, **kwargs)

    return fake, calls


class StepsReadResilienceTests(unittest.TestCase):
    def test_stores_for_same_root_share_one_lock(self):
        """API 每请求新建 Store，锁必须按根目录共享，否则线程锁形同虚设。"""
        with tempfile.TemporaryDirectory() as directory:
            first = RecordStore(directory)
            second = RecordStore(directory)
            self.assertIs(first._lock, second._lock)

    def test_transient_sharing_violation_is_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RecordStore(directory)
            store.run_dir("run").mkdir(parents=True, exist_ok=True)
            store.steps_path("run").write_text(
                json.dumps({"identify_carrier": {"name": "identify_carrier"}}), encoding="utf-8"
            )
            fake, calls = _flaky_read_text(store.steps_path("run"), failures=3)
            with patch.object(Path, "read_text", fake):
                snapshot = store.steps_snapshot("run")
            self.assertIn("identify_carrier", snapshot)
            self.assertEqual(calls["count"], 4)

    def test_unreadable_primary_falls_back_to_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RecordStore(directory)
            run_dir = store.run_dir("run")
            run_dir.mkdir(parents=True, exist_ok=True)
            store.steps_path("run").write_text(json.dumps({"broken": {}}), encoding="utf-8")
            (run_dir / "steps.json.bak").write_text(
                json.dumps({"identify_carrier": {"name": "identify_carrier"}}), encoding="utf-8"
            )
            fake, _ = _flaky_read_text(store.steps_path("run"), failures=None)
            with patch.object(Path, "read_text", fake), self.assertLogs(
                "infrastructure.storage.store", level="WARNING"
            ) as logs:
                snapshot = store.steps_snapshot("run")
            self.assertIn("identify_carrier", snapshot)
            self.assertIn("回退到写前快照", "\n".join(logs.output))

    def test_corrupt_primary_falls_back_to_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RecordStore(directory)
            run_dir = store.run_dir("run")
            run_dir.mkdir(parents=True, exist_ok=True)
            store.steps_path("run").write_text('{"identify_carrier": ', encoding="utf-8")
            (run_dir / "steps.json.bak").write_text(
                json.dumps({"identify_carrier": {"name": "identify_carrier"}}), encoding="utf-8"
            )
            with self.assertLogs("infrastructure.storage.store", level="WARNING"):
                snapshot = store.steps_snapshot("run")
            self.assertIn("identify_carrier", snapshot)

    def test_unreadable_primary_and_backup_still_raise(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RecordStore(directory)
            store.run_dir("run").mkdir(parents=True, exist_ok=True)
            store.steps_path("run").write_text(json.dumps({"broken": {}}), encoding="utf-8")
            fake, _ = _flaky_read_text(store.steps_path("run"), failures=None)
            with patch.object(Path, "read_text", fake):
                with self.assertRaises(PermissionError):
                    store.steps_snapshot("run")

    def test_writes_keep_succeeding_while_board_polls(self):
        """原始故障场景：写线程持续落盘，读线程持续投影任务板。"""
        with tempfile.TemporaryDirectory() as directory:
            writer = RecordStore(directory)
            reader = RecordStore(directory)  # 模拟另一个请求线程
            writer.run_dir("run").mkdir(parents=True, exist_ok=True)
            failures: list[BaseException] = []
            stop = threading.Event()

            def poll():
                while not stop.is_set():
                    try:
                        reader.list_steps("run")
                    except BaseException as error:  # noqa: BLE001 - 回归用例，记录任意失败
                        failures.append(error)
                        return

            thread = threading.Thread(target=poll, daemon=True)
            thread.start()
            try:
                for index in range(40):
                    writer.save("run", StepRecord(
                        name=f"step_{index}", title="t", status="succeeded",
                    ))
            finally:
                stop.set()
                thread.join(timeout=10)
            self.assertEqual(failures, [])
            self.assertEqual(len(writer.list_steps("run")), 40)


if __name__ == "__main__":
    unittest.main()
