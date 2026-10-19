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
    assert report["message"] == "后台工作流异常；请检查服务端日志中的完整堆栈。"


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


def test_value_error_in_step_is_recorded_without_original_exception_text(tmp_path, monkeypatch):
    from pydantic import BaseModel

    from app.application.services import WorkflowServices
    from app.infrastructure.records.store import RecordStore
    from app.workflow.runner import run_stage
    import app.workflow.runner as runner_module
    from app.workflow.spec import StepSpec

    class Input(BaseModel):
        value: str

    class Output(BaseModel):
        passed: bool = False

    def runner(_ctx, _data):
        raise ValueError("raw-secret-from-provider")

    store = RecordStore(tmp_path)
    store.create_run("run-validation", {})
    spec = StepSpec(
        name="validation_probe",
        title="validation probe",
        runner=runner,
        input_model=Input,
        output_model=Output,
        input_builder=lambda _state: {"value": "ok"},
        task_level=False,
    )
    monkeypatch.setattr(runner_module, "parse_input", lambda _name, _payload: Input(value="ok"))

    result = run_stage(spec, store, WorkflowServices(), {"run_id": "run-validation"})
    assert result.status == "failed"
    record = store.list_steps("run-validation")[0]
    assert record.status == "failed"
    assert "raw-secret-from-provider" not in str(record.model_dump())
    assert "validation_probe" in str(record.error.message)
