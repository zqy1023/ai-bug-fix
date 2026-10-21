from __future__ import annotations

from app.infrastructure.records.lifecycle import LifecycleStore
from workbench.api import _task_status


def test_run_lifecycle_tracks_active_and_terminal_transitions(tmp_path):
    lifecycle = LifecycleStore(tmp_path)

    queued = lifecycle.update_run("run-1", status="queued", phase="queued")
    assert queued["status"] == "queued"
    assert queued["finished_at"] is None

    running = lifecycle.update_run(
        "run-1", status="running", phase="sync_source_repo",
        current_step="sync_source_repo", attempt=2,
    )
    assert running["status"] == "running"
    assert running["current_step"] == "sync_source_repo"
    assert running["attempt"] == 2
    assert running["heartbeat_at"]

    failed = lifecycle.update_run(
        "run-1", status="failed", terminal_reason="source sync failed",
        error_type="SourceRepoError",
    )
    assert failed["status"] == "failed"
    assert failed["finished_at"]
    assert failed["terminal_reason"] == "source sync failed"


def test_retrying_run_clears_previous_terminal_timestamp(tmp_path):
    lifecycle = LifecycleStore(tmp_path)
    lifecycle.update_run("run-2", status="failed", terminal_reason="previous attempt failed")

    retried = lifecycle.update_run(
        "run-2", status="queued", phase="retry_queued",
        started_at=None, finished_at=None, error={},
        terminal_reason="", error_type="", error_message="",
    )

    assert retried["status"] == "queued"
    assert retried["finished_at"] is None
    assert retried["terminal_reason"] == ""
    # Active states receive a fresh start timestamp and heartbeat.
    assert retried["started_at"]
    assert retried["heartbeat_at"]


def test_task_lifecycle_records_current_node_attempt_and_final_failure(tmp_path):
    lifecycle = LifecycleStore(tmp_path)

    running = lifecycle.update_task(
        "run-3", "task-3", status="running",
        current_step="repair_agent", attempt=2,
    )
    assert running["status"] == "running"
    assert running["current_step"] == "repair_agent"
    assert running["attempt"] == 2

    failed = lifecycle.update_task(
        "run-3", "task-3", status="failed",
        current_step="repair_agent", terminal_step="repair_agent",
        terminal_reason="agent failed", next_action="retry this node",
    )
    assert failed["status"] == "failed"
    assert failed["terminal_step"] == "repair_agent"
    assert failed["next_action"] == "retry this node"


def test_active_step_status_overrides_an_earlier_failed_step():
    steps = [
        {"name": "repair_agent", "status": "failed"},
        {"name": "prepare_workspace", "status": "running"},
    ]

    assert _task_status(steps, approval=None) == "RUNNING"


def test_pending_approval_is_not_misreported_as_failed():
    steps = [
        {"name": "repair_agent", "status": "failed"},
        {"name": "request_approval", "status": "waiting", "output": {"decision": "PENDING"}},
    ]

    assert _task_status(steps, approval={"status": "waiting", "output": {"decision": "PENDING"}}) == "WAITING_APPROVAL"
