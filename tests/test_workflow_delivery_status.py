from types import SimpleNamespace

from app.workflow.routes import DELIVER, END, FINALIZE, after_delivery, after_finalize
from app.workflow.steps.finalize_execution.node import _delivery_status
from app.infrastructure.redaction import public_exception
from app.workflow.steps.prepare_delivery import _input as build_delivery_input


def test_source_delivery_failure_is_not_reclassified_as_analysis_only():
    state = {
        "last_result": "failed",
        "delivery": {"delivery_status": "FAILED"},
        "locate": {
            "status": "LOCATED",
            "recommended_scope": "carrier",
            "category": "carrier",
        },
        "review": {"verdict": "PASS"},
        "ai_workspace_path": "/workspace/task",
    }

    assert after_delivery(state) == FINALIZE
    assert after_finalize(state) == END


def test_explicit_legacy_analysis_task_delivers_report_then_ends():
    state = {
        "last_result": "passed",
        "delivery": {"delivery_status": "SUCCESS"},
        "evidence": {"legacy_only": True},
        "locate": {"status": "LOCATED", "recommended_scope": "carrier", "category": "carrier"},
        "ai_workspace_path": "/workspace/task",
    }

    assert after_finalize(state) == DELIVER
    assert after_delivery(state) == END


def test_manual_review_is_analysis_only_but_failed_report_delivery_is_failure_path():
    state = {
        "last_result": "passed",
        "delivery": {},
        "review": {"verdict": "MANUAL"},
        "locate": {
            "status": "LOCATED",
            "recommended_scope": "carrier",
            "category": "carrier",
        },
        "ai_workspace_path": "/workspace/task",
    }
    assert after_finalize(state) == DELIVER

    failed_delivery = {
        **state,
        "last_result": "failed",
        "delivery": {"delivery_status": "FAILED"},
    }
    assert after_delivery(failed_delivery) == FINALIZE
    assert after_finalize(failed_delivery) == END


def test_delivery_execution_failure_takes_precedence_over_manual_review():
    records = [
        SimpleNamespace(name="review_agent", status="succeeded", result="MANUAL"),
        SimpleNamespace(name="prepare_delivery", status="failed", result=""),
    ]

    assert _delivery_status(records, "") == "FAILED"

def test_locate_agent_execution_failure_is_not_reclassified_as_analysis_only():
    state = {
        "last_result": "failed",
        "evidence": {"legacy_only": True},
        "locate": {"status": "FAILED"},
        "ai_workspace_path": "/workspace/task",
    }

    assert after_finalize(state) == END

def test_attribute_error_summary_keeps_safe_missing_attribute_detail():
    message = public_exception(
        AttributeError("'WorkspaceManager' object has no attribute 'project_root'"),
        prefix="交付失败",
    )

    assert "WorkspaceManager" in message
    assert "project_root" in message



def test_failed_final_summary_does_not_route_into_analysis_report_delivery():
    state = {
        "last_result": "passed",
        "task_summary": {"delivery_status": "FAILED"},
        "delivery": {},
        "review": {"verdict": "MANUAL"},
        "locate": {
            "status": "LOCATED",
            "recommended_scope": "carrier",
            "category": "carrier",
        },
        "ai_workspace_path": "/workspace/task",
    }

    assert after_finalize(state) == END


def test_delivery_input_does_not_infer_analysis_only_from_failure_status():
    state = {
        "task_id": "TestCarrier__framework.track_server__issue",
        "task": {"task_id": "TestCarrier__framework.track_server__issue",
                 "carrier": "TestCarrier", "biztype": "framework.track_server"},
        "task_summary": {"delivery_status": "FAILED"},
        "delivery": {"delivery_status": "FAILED"},
        "review": {"verdict": "PASS", "summary": "review passed"},
        "locate": {
            "status": "LOCATED",
            "recommended_scope": "carrier",
            "category": "carrier",
        },
        "evidence": {"carrier_dir": "gathers/TestCarrier"},
        "framework": "gathers",
        "ai_workspace_path": "/workspace/task",
        "changed_files": ["gathers/TestCarrier/main.py"],
        "problem": {"error": "test error", "fingerprint": "test"},
    }

    result = build_delivery_input(state)

    assert result["analysis_only"] is False
    assert result["report_paths"] == []


def test_failed_locate_status_is_not_analysis_only():
    state = {
        "task_id": "TestCarrier__framework.track_server__issue",
        "task": {"task_id": "TestCarrier__framework.track_server__issue",
                 "carrier": "TestCarrier", "biztype": "framework.track_server"},
        "review": {},
        "locate": {"status": "FAILED"},
        "framework": "gathers",
        "ai_workspace_path": "/workspace/task",
        "changed_files": [],
        "problem": {"error": "test error", "fingerprint": "test"},
    }

    result = build_delivery_input(state)

    assert result["analysis_only"] is False
    assert result["report_paths"] == []
