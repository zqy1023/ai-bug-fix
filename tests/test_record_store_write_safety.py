from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.infrastructure.records.store import RecordStore, _run_thread_lock


def test_write_steps_uses_unique_temporary_file_and_keeps_valid_json(tmp_path: Path) -> None:
    store = RecordStore(tmp_path)

    store._write_steps("run-1", {"first": {"name": "first"}})
    store._write_steps("run-1", {"second": {"name": "second"}})

    path = store.steps_path("run-1")
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "second": {"name": "second"}
    }
    assert not list(path.parent.glob(".steps.json.*.tmp"))
    assert not path.with_suffix(".tmp").exists()


def test_write_steps_retries_transient_windows_replace_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RecordStore(tmp_path)
    real_replace = os.replace
    attempts = 0

    def flaky_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("simulated transient file lock")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", flaky_replace)
    store._write_steps("run-2", {"safe": True})

    assert attempts == 3
    assert json.loads(store.steps_path("run-2").read_text(encoding="utf-8")) == {
        "safe": True
    }


def test_record_stores_share_per_run_thread_lock(tmp_path: Path) -> None:
    first = RecordStore(tmp_path)
    second = RecordStore(tmp_path)
    lock_path = first.run_dir("run-3") / ".steps.lock"

    assert _run_thread_lock(lock_path) is _run_thread_lock(
        second.run_dir("run-3") / ".steps.lock"
    )
    assert _run_thread_lock(lock_path) is not _run_thread_lock(
        first.run_dir("another-run") / ".steps.lock"
    )
