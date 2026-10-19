from fastapi.testclient import TestClient

import workbench.api as workbench_api
from workbench.api import app



def test_v1_resume_rejects_non_resumable_checkpoint_and_releases_lock(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(workbench_api, "_v1_detail", lambda run_id: {
        "run_id": run_id, "status": "failed", "checkpoint": {"resumable": False}
    })
    response = client.post("/api/v1/runs/resume-demo/resume")
    assert response.status_code == 409
    with workbench_api._RUN_LOCKS_GUARD:
        lock = workbench_api._RUN_LOCKS["resume-demo"]
    assert lock.acquire(blocking=False)
    lock.release()


def test_health_endpoint_is_available():
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code in {200, 503}


def test_invalid_approval_decision_is_rejected():
    client = TestClient(app)
    response = client.post(
        "/api/runs/demo/tasks/task-demo/approval",
        json={"decision": "MAYBE"},
    )
    assert response.status_code == 422


def test_manual_intervention_api_routes_are_removed():
    client = TestClient(app)
    assert client.get("/api/v1/manual-interventions").status_code == 404
    assert client.post(
        "/api/v1/manual-interventions/run-1:task-1/decision",
        json={"decision": "terminate", "reason": "done"},
    ).status_code == 404


def test_v1_contract_exposes_dashboard_and_paginated_runs():
    client = TestClient(app)
    dashboard = client.get("/api/v1/dashboard/summary")
    assert dashboard.status_code in {200, 503}
    if dashboard.status_code == 200:
        body = dashboard.json()
        assert {"active_runs", "waiting_approvals", "daily_trend", "recent_runs"} <= body.keys()
    runs = client.get("/api/v1/runs?page=1&page_size=5")
    assert runs.status_code in {200, 503}
    if runs.status_code == 200:
        body = runs.json()
        assert body["page"] == 1
        assert body["page_size"] == 5
        assert isinstance(body["items"], list)


def test_v1_start_requires_idempotency_key():
    client = TestClient(app)
    response = client.post("/api/v1/runs", json={
        "log_days": 1, "log_limit": 0, "review_mode": "agent", "max_attempts": 3
    })
    assert response.status_code in {400, 409, 503}



def test_model_call_audit_endpoint_requires_existing_run():
    client = TestClient(app)
    response = client.get("/api/v1/runs/nonexistent-run/model-calls")
    assert response.status_code in {404, 503}


def test_control_audit_endpoint_requires_existing_run():
    client = TestClient(app)
    response = client.get("/api/v1/runs/nonexistent-run/control-audit")
    assert response.status_code in {404, 503}



def test_v1_start_persists_run_before_scheduling_background_task(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.infrastructure.records.store import RecordStore
    from workbench.api import V1StartRunRequest

    runtime_dir = tmp_path / "runtime"
    config = SimpleNamespace(runtime_dir=runtime_dir, source_repo=tmp_path / "source")
    store = RecordStore(runtime_dir)
    monkeypatch.setattr(workbench_api, "_config", lambda: config)
    monkeypatch.setattr(workbench_api, "_store", lambda: store)

    def prepare_run(run_id, *, overrides=None, queued=False):
        store.create_run(run_id, {
            "source_repo_path": str(config.source_repo),
            "log_days": overrides["log_days"],
            "log_limit": overrides["log_limit"],
            "review_mode": overrides["review_mode"],
            "max_attempts": overrides["max_attempts"],
            "startup_state": "queued" if queued else "started",
        })

    monkeypatch.setattr("app.entrypoint.prepare_run", prepare_run)

    class CapturingBackground:
        def __init__(self):
            self.tasks = []

        def add_task(self, function, *args, **kwargs):
            run_id = args[0]
            assert store.run_dir(run_id).joinpath("run.json").is_file()
            self.tasks.append((function, args, kwargs))

    background = CapturingBackground()
    result = workbench_api.v1_start_run(
        V1StartRunRequest(log_days=1, log_limit=0, review_mode="agent", max_attempts=3),
        background,
        idempotency_key="start-run-test-key",
    )

    assert result["status"] == "accepted"
    assert result["idempotent_replay"] is False
    assert len(background.tasks) == 1
    assert store.load_run(result["run_id"])["log_days"] == 1

    # The fake background scheduler does not run the callback, so release the acquired run lock.
    workbench_api._RUN_LOCKS[result["run_id"]].release()
    with workbench_api._RUN_LOCKS_GUARD:
        workbench_api._STARTED_RUNS.discard(result["run_id"])


def test_v1_approval_projection_uses_real_repair_plan_and_hides_file_contents(monkeypatch):
    monkeypatch.setattr(workbench_api, "_store", lambda: object())
    monkeypatch.setattr(workbench_api, "list_approvals", lambda: {
        "items": [{
            "run_id": "run-plan",
            "task_id": "task-plan",
            "carrier": "ExampleCarrier",
            "biztype": "track_server",
            "updated_at": "2026-10-16T00:00:00+00:00",
            "approval_step": {"input": {"mode": "change_approval", "recommended_scope": "project"}, "output": {}},
        }],
    })
    monkeypatch.setattr(workbench_api, "_run_payload", lambda _store, _run_id: {
        "tasks": [{
            "task_id": "task-plan",
            "steps": [{
                "name": "repair_agent",
                "output": {
                    "summary": "共享层响应解析需要调整",
                    "changed_files": [],
                    "change_plan": {
                        "scope": "project",
                        "rationale": "统一处理上游空响应",
                        "requires_human_authorization": True,
                        "edits": [{
                            "path": "common/redis_utils.py",
                            "action": "update",
                            "note": "增加空响应保护",
                            "content": "SENSITIVE_FULL_FILE_CONTENT",
                        }],
                    },
                },
            }],
        }],
    })

    result = workbench_api.v1_list_approvals("pending", None)

    assert result["total"] == 1
    item = result["items"][0]
    assert item["scope"] == "project"
    assert item["changed_files"] == ["common/redis_utils.py"]
    assert item["change_plan"]["edits"][0]["path"] == "common/redis_utils.py"
    assert "content" not in item["change_plan"]["edits"][0]
    assert "SENSITIVE_FULL_FILE_CONTENT" not in str(item)


def test_latest_approval_step_prefers_current_interrupt_over_historical_approval():
    old = {"name": "request_approval", "status": "succeeded", "output": {"decision": "APPROVE"}}
    latest = {"name": "request_approval", "status": "running", "output": {"decision": "PENDING"}}

    assert workbench_api._latest_approval_step([
        old,
        {"name": "repair_agent", "status": "succeeded"},
        latest,
    ]) is latest
    assert workbench_api._approval_pending(workbench_api._latest_approval_step([old, latest])) is True


def test_approval_resume_passes_decision_even_when_parent_snapshot_hides_nested_interrupt(monkeypatch, tmp_path):
    import threading
    from types import SimpleNamespace

    from langgraph.types import Command
    import app.entrypoint
    import app.workflow.wiring

    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    store = SimpleNamespace(run_dir=lambda _run_id: run_dir)
    invoked = {}

    class FakeGraph:
        def get_state(self, _config):
            # Some LangGraph versions don't expose nested subgraph interrupts in parent task.interrupts.
            return SimpleNamespace(tasks=[])

        def invoke(self, command, config=None):
            invoked["command"] = command
            invoked["config"] = config
            return {}

    monkeypatch.setattr(workbench_api, "_store", lambda: store)
    monkeypatch.setattr(app.workflow.wiring, "build_graph", lambda store=None: FakeGraph())
    monkeypatch.setattr(app.entrypoint, "_write_run_report_if_terminal", lambda *args, **kwargs: None)
    monkeypatch.setattr(workbench_api, "_record_background_failure", lambda *args, **kwargs: None)

    lock = threading.Lock()
    assert lock.acquire(blocking=False)
    workbench_api._resume_workflow_background(
        "run-1",
        lock,
        {"decision": "APPROVE", "approval_fingerprint": "fingerprint-1"},
    )

    command = invoked["command"]
    assert isinstance(command, Command)
    assert command.resume == {"decision": "APPROVE", "approval_fingerprint": "fingerprint-1"}
    assert lock.acquire(blocking=False) is True
    lock.release()

