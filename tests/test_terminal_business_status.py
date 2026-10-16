from app.application.audit import StepRecord
from app.workflow.steps.finalize_execution.node import _delivery_status
from workbench.api import _derive_run_status


def test_manual_business_result_is_not_reported_as_failed():
    locate = StepRecord(
        name="locate_agent",
        title="根因定位",
        status="succeeded",
        result="MANUAL",
        summary="证据不足，需要人工介入",
    )
    assert _delivery_status([locate], "") == "MANUAL"


def test_manual_terminal_status_is_case_insensitive():
    assert _delivery_status([], "MANUAL") == "MANUAL"
    assert _delivery_status([], "manual") == "MANUAL"


def test_run_with_manual_task_is_not_marked_succeeded():
    assert _derive_run_status([], [{"status": "MANUAL"}]) == "partial_failed"


def test_mixed_success_and_failure_is_partial_failed():
    assert _derive_run_status([], [{"status": "SUCCESS"}, {"status": "FAILED"}]) == "partial_failed"


def test_all_failed_tasks_keep_failed_run_status():
    assert _derive_run_status([], [{"status": "FAILED"}, {"status": "FAIL"}]) == "failed"


def test_background_exception_is_persisted_as_a_safe_terminal_report(tmp_path, monkeypatch):
    import json
    import workbench.api as workbench_api

    class FakeStore:
        def run_dir(self, run_id):
            return tmp_path / run_id

    run_dir = tmp_path / "run-123"
    run_dir.mkdir()
    monkeypatch.setattr(workbench_api, "_store", lambda: FakeStore())

    workbench_api._record_background_failure("run-123", RuntimeError("secret-token-should-not-leak"))

    report = json.loads((run_dir / "reports" / "run_failure.json").read_text(encoding="utf-8"))
    assert report["status"] == "FAILED"
    assert report["error_type"] == "RuntimeError"
    assert "secret-token-should-not-leak" not in json.dumps(report)


def test_persisted_business_result_uses_model_output_contract():
    from app.agents.locate.models import LocateOutput
    from app.agents.review.models import ReviewView
    from app.application.audit import map_outcome

    assert map_outcome(
        kind="ok",
        output={"status": "MANUAL", "passed": False},
        output_model=LocateOutput,
    ) == ("succeeded", "MANUAL")
    assert map_outcome(
        kind="ok",
        output={"verdict": "PASS", "passed": True},
        output_model=ReviewView,
    ) == ("succeeded", "PASS")
    assert map_outcome(
        kind="ok",
        output={"delivery_status": "SUCCESS"},
    ) == ("succeeded", "SUCCESS")


def test_business_conclusions_drive_routes_without_relabeling_step_execution():
    from app.workflow.runner import _route_status

    assert _route_status("MANUAL") == "manual"
    assert _route_status("INSUFFICIENT_EVIDENCE") == "manual"
    assert _route_status("FAIL") == "failed"
    assert _route_status("SUCCESS") == "passed"
    assert _route_status(None) == "passed"


def test_cancelled_terminal_status_is_preserved_in_final_report():
    assert _delivery_status([], "CANCELLED") == "CANCELLED"
