from types import SimpleNamespace

from app.workflow.routes import APPROVE, DELIVER, END, FINALIZE, after_delivery, after_finalize, after_review
from app.workflow.steps.finalize_execution.node import _delivery_status, _terminal_details
from app.infrastructure.redaction import public_exception
from app.domain.errors import StepFailed
from app.workflow.steps.prepare_delivery.models import DeliveryInput
from app.workflow.steps.prepare_delivery.node import run as run_prepare_delivery
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


def test_legacy_carrier_uses_source_delivery_and_approval():
    state = {
        "last_result": "passed",
        "delivery": {"delivery_status": "SUCCESS"},
        "evidence": {"legacy_only": True},
        "locate": {"status": "LOCATED", "recommended_scope": "carrier", "category": "carrier"},
        "ai_workspace_path": "/workspace/task",
    }

    assert after_finalize(state) == END
    assert after_delivery(state) == APPROVE


def test_review_manual_submits_source_branch_before_final_approval():
    state = {
        "last_result": "passed",
        "delivery": {},
        "review": {"verdict": "MANUAL", "summary": "需要人工复核"},
        "locate": {
            "status": "LOCATED",
            "recommended_scope": "carrier",
            "category": "carrier",
        },
        "evidence": {"carrier_dir": "gathers/TestCarrier"},
        "framework": "gathers",
        "ai_workspace_path": "/workspace/task",
        "changed_files": ["gathers/TestCarrier/main.py"],
        "task_id": "TestCarrier__framework.track_server__issue",
        "task": {
            "task_id": "TestCarrier__framework.track_server__issue",
            "carrier": "TestCarrier",
            "biztype": "framework.track_server",
        },
        "problem": {"error": "test error", "fingerprint": "test"},
    }

    # MANUAL 是“源码分支交付后需要人复核”，不是分析报告分流，也不是已进入审批态。
    assert after_review(state) == DELIVER
    delivery_input = build_delivery_input(state)
    assert delivery_input["analysis_only"] is False
    assert delivery_input["review_decision"] == "MANUAL"
    assert delivery_input["report_paths"] == []

    submitted = {
        **state,
        "last_result": "passed",
        "delivery": {"delivery_status": "SUCCESS"},
    }
    # 只有分支提交成功之后才进入最终人工审批。
    assert after_delivery(submitted) == APPROVE

    failed_delivery = {
        **submitted,
        "last_result": "failed",
        "delivery": {"delivery_status": "FAILED"},
    }
    assert after_delivery(failed_delivery) == FINALIZE


def test_delivery_execution_failure_takes_precedence_over_manual_review():
    records = [
        SimpleNamespace(name="review_agent", status="succeeded", result="MANUAL"),
        SimpleNamespace(name="prepare_delivery", status="failed", result=""),
    ]

    assert _delivery_status(records, "") == "FAILED"

def test_final_approval_converts_review_manual_into_success_after_source_commit():
    records = [
        SimpleNamespace(
            name="review_agent", status="succeeded", result="MANUAL",
            output={"verdict": "MANUAL"},
        ),
        SimpleNamespace(
            name="prepare_delivery", status="succeeded", result="",
            input={"analysis_only": False},
            output={"commit": "abc123", "pushed": True, "local_branch": False},
        ),
        SimpleNamespace(
            name="request_approval", status="succeeded", result="",
            output={"decision": "APPROVE", "passed": True},
        ),
    ]

    assert _delivery_status(records, "MANUAL") == "SUCCESS"

    terminal_step, terminal_reason, next_action = _terminal_details(
        records, "MANUAL", "SUCCESS"
    )
    assert terminal_step == "request_approval"
    assert "最终人工审批" in terminal_reason
    assert "手动合并" in next_action

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


def test_failed_locate_does_not_use_stale_legacy_candidate_as_business_routing():
    state = {
        "task_id": "TestCarrier__framework.track_server__issue",
        "task": {"task_id": "TestCarrier__framework.track_server__issue",
                 "carrier": "TestCarrier", "biztype": "framework.track_server"},
        "review": {},
        "locate": {
            "status": "FAILED",
            "candidate_files": ["spiders/TestCarrier/main.py"],
        },
        "evidence": {"carrier_dir": "gathers/TestCarrier"},
        "framework": "gathers",
        "ai_workspace_path": "/workspace/task",
        "changed_files": [],
        "problem": {"error": "test error", "fingerprint": "test"},
    }

    result = build_delivery_input(state)

    assert result["analysis_only"] is False
    assert result["report_paths"] == []


def test_delivery_accepts_review_manual_only_for_final_human_approval():
    class CapturingDelivery:
        def __init__(self):
            self.payload = None

        def deliver(self, state):
            self.payload = state
            return {
                "repair_branch": state["repair_branch"],
                "commit": "abc123",
                "pushed": True,
                "local_branch": False,
            }

    delivery = CapturingDelivery()
    context = SimpleNamespace(
        run=SimpleNamespace(run_id="run-test"),
        services=SimpleNamespace(delivery=delivery),
    )
    data = DeliveryInput(
        task_id="Carrier__biztype__issue",
        carrier="Carrier",
        repair_branch="ai-bug-tasks/biztype/Carrier-issue",
        ai_workspace_path="/workspace/Carrier",
        changed_files=["gathers/Carrier/main.py"],
        review_decision="MANUAL",
        analysis_only=False,
    )

    output = run_prepare_delivery(context, data)

    assert output.commit == "abc123"
    assert delivery.payload["analysis_only"] is False
    assert delivery.payload["changed_files"] == ["gathers/Carrier/main.py"]


def test_delivery_attribute_error_writes_safe_trace_frames_into_step_output():
    class BrokenDelivery:
        def deliver(self, state):
            raise AttributeError("'DeliveryAdapter' object has no attribute 'deliver_report'")

    context = SimpleNamespace(
        run=SimpleNamespace(run_id="run-test"),
        services=SimpleNamespace(delivery=BrokenDelivery()),
    )
    data = DeliveryInput(
        task_id="Carrier__biztype__issue",
        carrier="Carrier",
        repair_branch="ai-bug-tasks/biztype/Carrier-issue",
        ai_workspace_path="/workspace/Carrier",
        changed_files=[],
        analysis_only=True,
        report_paths=["gathers/Carrier/final.json"],
    )

    try:
        run_prepare_delivery(context, data)
    except StepFailed as error:
        assert error.output["exception_type"] == "AttributeError"
        assert "deliver_report" in error.output["reason"]
        assert error.output["diagnostic_frames"]
        assert any(frame["function"] == "run" for frame in error.output["diagnostic_frames"])
    else:
        raise AssertionError("Expected delivery AttributeError to fail the step")
