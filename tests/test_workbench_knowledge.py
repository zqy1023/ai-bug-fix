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


def test_prepare_evidence_retrieves_only_published_cases_and_records_usage(monkeypatch, tmp_path):
    from app.workflow.steps.prepare_evidence import node as evidence_node

    _configure_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(
        evidence_node,
        "load_config",
        lambda: SimpleNamespace(runtime_dir=tmp_path),
    )
    connection = workbench_api._control_db()
    try:
        connection.execute(
            """INSERT INTO repair_knowledge_cases
            (case_id,title,fingerprint,carrier,biztype,root_cause,evidence,change_summary,
             regression_passed,review_passed,status,source_run_id,source_task_id,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,'published','','',?,?)""",
            (
                "case-published", "ExampleCarrier response parsing", "fingerprint",
                "ExampleCarrier", "Brazil", "Upstream response omitted optional field",
                "KeyError response parsing", "Use a safe default", 1, 1,
                "2026-10-16T00:00:00+00:00", "2026-10-16T00:00:00+00:00",
            ),
        )
        connection.execute(
            """INSERT INTO repair_knowledge_cases
            (case_id,title,fingerprint,carrier,biztype,root_cause,evidence,change_summary,
             regression_passed,review_passed,status,source_run_id,source_task_id,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,'candidate','','',?,?)""",
            (
                "case-candidate", "ExampleCarrier response parsing draft", "draft",
                "ExampleCarrier", "Brazil", "Unverified root cause", "Draft evidence",
                "Draft change", 0, 0, "2026-10-16T00:00:00+00:00", "2026-10-16T00:00:00+00:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    matches = evidence_node._published_knowledge(
        {"error": "KeyError response parsing optional field"},
        "ExampleCarrier",
        "Brazil",
        "HTML/XML/JSON 解析",
        "run-knowledge",
        "task-knowledge",
    )

    assert [item["case_id"] for item in matches] == ["case-published"]
    connection = workbench_api._control_db()
    try:
        usage = connection.execute(
            "SELECT run_id, task_id, case_id FROM knowledge_case_usage"
        ).fetchall()
    finally:
        connection.close()
    assert [(row["run_id"], row["task_id"], row["case_id"]) for row in usage] == [
        ("run-knowledge", "task-knowledge", "case-published")
    ]
