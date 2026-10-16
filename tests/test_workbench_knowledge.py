from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import workbench.api as workbench_api


def _configure_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(
        workbench_api,
        "_config",
        lambda: SimpleNamespace(runtime_dir=tmp_path),
    )


def test_knowledge_case_is_candidate_until_server_verified(monkeypatch, tmp_path):
    _configure_runtime(monkeypatch, tmp_path)
    created = workbench_api.v1_create_knowledge_case(
        workbench_api.KnowledgeCaseCreate(
            title="Example carrier response parsing regression",
            carrier="ExampleCarrier",
            biztype="Brazil",
            root_cause="Upstream response field changed",
            evidence="Source evidence points to response parsing",
            change_summary="Normalize optional response field",
        ),
        authorization=None,
    )

    assert created["status"] == "candidate"
    assert created["regression_passed"] is False
    assert created["review_passed"] is False

    with pytest.raises(HTTPException) as error:
        workbench_api.v1_update_knowledge_case_status(
            created["case_id"],
            workbench_api.KnowledgeCaseStatusUpdate(status="published"),
            authorization=None,
        )
    assert error.value.status_code == 409


def test_task_capture_is_idempotent_and_uses_persisted_gate_results(monkeypatch, tmp_path):
    _configure_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(workbench_api, "_v1_detail", lambda _run_id: {
        "status": "succeeded",
        "tasks": [{
            "task_id": "task-1",
            "carrier": "ExampleCarrier",
            "biztype": "Brazil",
            "status": "SUCCESS",
            "problem": "KeyError response field",
            "report": {"result": "SUCCESS", "summary": "Recovered response parsing"},
            "steps": [
                {"name": "locate_agent", "output": {
                    "root_cause": "Upstream response field became optional",
                    "facts": ["The response omitted the field"],
                    "source_evidence": [{"path": "gathers/ExampleCarrier/main.py", "finding": "Direct key access"}],
                    "evidence_gaps": [],
                }},
                {"name": "repair_agent", "output": {
                    "summary": "Use a safe default for the optional field",
                    "changed_files": ["gathers/ExampleCarrier/main.py"],
                }},
                {"name": "run_regression", "result": "passed", "output": {"passed": True}},
                {"name": "review_agent", "output": {"verdict": "PASS"}},
            ],
        }],
    })

    first = workbench_api.v1_capture_knowledge_from_task("run-1", "task-1", authorization=None)
    second = workbench_api.v1_capture_knowledge_from_task("run-1", "task-1", authorization=None)

    assert first["case_id"] == second["case_id"]
    assert first["status"] == "candidate"
    assert first["regression_passed"] is True
    assert first["review_passed"] is True
    assert first["source_run_id"] == "run-1"
    assert first["source_task_id"] == "task-1"

    listed = workbench_api.v1_list_knowledge_cases(
        q="ExampleCarrier", status="all", page=1, page_size=20, _=None
    )
    assert listed["total"] == 1
    assert listed["items"][0]["case_id"] == first["case_id"]


def test_knowledge_usage_summary_is_present(monkeypatch, tmp_path):
    _configure_runtime(monkeypatch, tmp_path)
    result = workbench_api.v1_list_knowledge_cases(
        q="", status="all", page=1, page_size=20, _=None
    )
    assert result["summary"]["retrievals"] == 0
    assert result["summary"]["successful_reuses"] == 0
