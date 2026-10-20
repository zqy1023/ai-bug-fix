from types import SimpleNamespace

from app.workflow.routes import DELIVER, END, FINALIZE, after_delivery, after_finalize
from app.workflow.steps.finalize_execution.node import _delivery_status


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
