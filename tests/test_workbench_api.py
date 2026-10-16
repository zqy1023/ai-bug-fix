from fastapi.testclient import TestClient

import workbench.api as workbench_api
from workbench.api import app


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



def test_manual_intervention_endpoint_lists_manual_locate_without_treating_it_as_approval(monkeypatch):
    monkeypatch.setattr(workbench_api, "_store", lambda: object())
    monkeypatch.setattr(workbench_api, "_all_run_ids", lambda _store: ["run-manual"])
    monkeypatch.setattr(workbench_api, "_run_payload", lambda _store, _run_id: {
        "updated_at": "2026-10-16T00:00:00+00:00",
        "tasks": [{
            "task_id": "task-1",
            "carrier": "ExampleCarrier",
            "biztype": "Brazil",
            "status": "MANUAL",
            "approval_pending": False,
            "steps": [{
                "name": "locate_agent",
                "result": "MANUAL",
                "summary": "缺少关键源码证据",
                "output": {"status": "MANUAL", "summary": "缺少关键源码证据"},
            }],
        }],
    })

    result = workbench_api.v1_list_manual_interventions(None)

    assert result["total"] == 1
    item = result["items"][0]
    assert item["intervention_id"] == "run-manual:task-1"
    assert item["step"] == "locate_agent"
    assert item["status"] == "open"
    assert item["summary"] == "缺少关键源码证据"


def test_manual_intervention_endpoint_recovers_legacy_locate_detail(monkeypatch):
    monkeypatch.setattr(workbench_api, "_store", lambda: object())
    monkeypatch.setattr(workbench_api, "_all_run_ids", lambda _store: ["run-legacy"])
    monkeypatch.setattr(workbench_api, "_run_payload", lambda _store, _run_id: {
        "updated_at": "2026-10-16T00:00:00+00:00",
        "tasks": [{
            "task_id": "task-legacy",
            "carrier": "ExampleCarrier",
            "biztype": "track_server",
            "status": "MANUAL",
            "approval_pending": False,
            "steps": [{
                "name": "locate_agent",
                "result": "MANUAL",
                "summary": "定位输出不满足证据或路径契约，停止并转人工",
                "output": {
                    "status": "MANUAL",
                    "summary": "定位输出不满足证据或路径契约，停止并转人工",
                    "evidence_gaps": ["candidate_files[0] 超出目标 carrier 目录：gathers/Other/main.py"],
                },
            }],
        }],
    })

    result = workbench_api.v1_list_manual_interventions(None)

    assert result["total"] == 1
    assert "gathers/Other/main.py" in result["items"][0]["summary"]
    assert result["items"][0]["summary"].startswith("定位输出契约校验失败：")


def test_v1_start_persists_run_before_scheduling_background_task(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.infrastructure.records.store import RecordStore
    from workbench.api import V1StartRunRequest

    runtime_dir = tmp_path / "runtime"
    config = SimpleNamespace(runtime_dir=runtime_dir, source_repo=tmp_path / "source")
    store = RecordStore(runtime_dir)
    monkeypatch.setattr(workbench_api, "_config", lambda: config)
    monkeypatch.setattr(workbench_api, "_store", lambda: store)

    def prepare_run(run_id, *, overrides=None):
        store.create_run(run_id, {
            "source_repo_path": str(config.source_repo),
            "log_days": overrides["log_days"],
            "log_limit": overrides["log_limit"],
            "review_mode": overrides["review_mode"],
            "max_attempts": overrides["max_attempts"],
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
