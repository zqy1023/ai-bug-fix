from types import SimpleNamespace

from app.entrypoint import _ensure_task_final_report
from app.workflow.steps.finalize_execution.node import _terminal_details


def _record(
    name: str,
    *,
    status: str = "succeeded",
    result: str | None = None,
    output: dict | None = None,
    summary: str = "",
    error: object | None = None,
    time: str = "2026-10-16T10:00:00+00:00",
):
    return SimpleNamespace(
        name=name, status=status, result=result, output=output or {},
        summary=summary, error=error, started_at=time, finished_at=time,
    )


def test_evidence_legacy_gate_report_contains_reason_and_next_action():
    reason = "目标只有 spiders/CouriersPlease，没有 gathers/CouriersPlease/main.py"
    step = _record(
        "prepare_evidence",
        output={"legacy_only": True, "legacy_reason": reason},
    )

    terminal_step, terminal_reason, next_action = _terminal_details([step], "manual", "MANUAL")

    assert terminal_step == "prepare_evidence"
    assert reason in terminal_reason
    assert next_action.strip()


def test_failed_step_report_preserves_recorded_error():
    step = _record(
        "run_regression",
        status="failed",
        error=SimpleNamespace(message="回归命令退出码 1"),
    )

    terminal_step, terminal_reason, next_action = _terminal_details([step], "", "FAILED")

    assert terminal_step == "run_regression"
    assert "回归命令退出码 1" in terminal_reason
    assert next_action.strip()


def test_success_and_unknown_terminal_states_always_have_reason():
    success = _record("prepare_delivery", output={"summary": "修复分支已准备"})
    _, success_reason, success_action = _terminal_details([success], "", "SUCCESS")
    _, unknown_reason, unknown_action = _terminal_details([], "", "UNKNOWN")

    assert success_reason.strip()
    assert success_action.strip()
    assert unknown_reason.strip()
    assert unknown_action.strip()


def test_run_terminal_recovery_writes_specific_task_report_for_evidence_gate():
    reason = "CouriersPlease only has a legacy spider and no supported gather entry point"
    evidence = _record("prepare_evidence", output={"legacy_only": True, "legacy_reason": reason})
    evidence.duration_ms = 31.0

    class Store:
        root = None

        def list_steps(self, run_id: str, task_id: str):
            return [evidence]

    report = _ensure_task_final_report(
        Store(), "run-1",
        {"task_id": "task-1", "carrier": "CouriersPlease", "biztype": "framework.track_server"},
        "task-1", {}, finished_at="2026-10-16T10:05:00+00:00", error=None,
    )

    assert report["result"] == "MANUAL"
    assert report["terminal_step"] == "prepare_evidence"
    assert reason in report["terminal_reason"]
    assert report["next_action"].strip()
