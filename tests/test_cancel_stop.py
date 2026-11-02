from langgraph.graph import END

from workflow.engine.graph import RUN_TASK, after_dispatch, collect


def test_cancelled_task_stops_dispatch_instead_of_fanning_out_cancelled_rows():
    state = {
        "tasks": [{"task_id": "a"}, {"task_id": "b"}],
        "task_cursor": 0,
        "task": {"task_id": "a"},
        "terminal_status": "CANCELLED",
    }

    patch = collect(state)

    assert patch["task_cursor"] == 1
    assert patch["stop_requested"] is True
    assert after_dispatch({**state, **patch}) == END


def test_normal_task_keeps_dispatching():
    state = {
        "tasks": [{"task_id": "a"}, {"task_id": "b"}],
        "task_cursor": 0,
        "task": {"task_id": "a"},
        "terminal_status": "SUCCESS",
    }

    patch = collect(state)

    assert "stop_requested" not in patch
    assert after_dispatch({**state, **patch}) == RUN_TASK


def test_control_store_read_error_fails_closed(tmp_path, monkeypatch):
    import sqlite3

    import pytest

    from infrastructure.storage import run_control

    database = tmp_path / "workbench-control.sqlite3"
    database.touch()

    def broken_connect(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(run_control.sqlite3, "connect", broken_connect)

    with pytest.raises(run_control.ControlStoreUnavailable):
        run_control.cancellation_requested(tmp_path, "run-1")

    with pytest.raises(run_control.ControlStoreUnavailable):
        run_control.pause_requested(tmp_path, "run-1")


def test_missing_control_store_means_no_persisted_request(tmp_path):
    from infrastructure.storage.run_control import (
        cancellation_requested,
        pause_requested,
    )

    assert cancellation_requested(tmp_path, "run-1") is False
    assert pause_requested(tmp_path, "run-1") is False
