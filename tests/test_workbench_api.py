from fastapi.testclient import TestClient

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
