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
