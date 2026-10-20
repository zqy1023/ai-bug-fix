from pathlib import Path
from types import SimpleNamespace

import pytest

from app.application.contracts import StepAudit, StepFailure, StepResult
from app.domain.errors import StepFailed
from app.workflow.routes import (
    DELIVER,
    FINALIZE,
    LOCATE,
    REGRESSION,
    REPAIR,
    REVIEW,
    VALIDATE,
    after_approval,
    after_retry,
)
from app.workflow.steps.request_approval.models import ApprovalInput


def test_after_retry_routes_each_allowed_rejection_target_to_the_named_phase():
    expected = {
        "locate_agent": LOCATE,
        "repair_agent": REPAIR,
        "validate_changes": VALIDATE,
        "run_regression": REGRESSION,
        "review_agent": REVIEW,
        "prepare_delivery": DELIVER,
    }
    for target, phase in expected.items():
        assert after_retry({"retry_target": target, "attempt": 1, "max_attempts": 3}) == phase

    assert after_retry({"retry_target": "untrusted-node", "attempt": 1, "max_attempts": 3}) == REPAIR
    assert after_retry({"retry_target": "repair_agent", "terminal_status": "CANCELLED"}) == FINALIZE


def test_rejection_routes_through_bounded_retry_not_success():
    state = {
        "last_result": "failed",
        "decision": "REJECT",
        "retry_target": "validate_changes",
        "attempt": 1,
        "max_attempts": 3,
    }

    from app.workflow.routes import RETRY

    assert after_approval(state) == RETRY
    assert after_retry(state) == VALIDATE
    assert after_retry({**state, "attempt": 3}) == VALIDATE


def test_approval_node_preserves_rejection_reason_and_target(monkeypatch):
    import app.workflow.steps.request_approval.node as approval_node

    resume = {
        "decision": "REJECT",
        "approval_fingerprint": "fingerprint",
        "reason": "问题验证不足，需要重跑原始问题验证。",
        "retry_target": "validate_changes",
    }
    monkeypatch.setattr(approval_node, "interrupt", lambda _payload: resume)

    data = ApprovalInput(
        task_id="Carrier__framework.track_server__issue",
        decision="PENDING",
        carrier="Carrier",
        ai_workspace_path="/workspace/task",
        changed_files=["gathers/Carrier/main.py"],
        baseline_dir="",
        approval_fingerprint="fingerprint",
    )
    ctx = SimpleNamespace(services=SimpleNamespace(delivery=None))

    with pytest.raises(StepFailed) as raised:
        approval_node.run(ctx, data)

    assert raised.value.result == "rejected"
    assert raised.value.output["decision"] == "REJECT"
    assert raised.value.output["retry_target"] == "validate_changes"
    assert raised.value.output["retry_reason"] == resume["reason"]


def test_runner_does_not_drop_rejection_retry_target(monkeypatch):
    import app.workflow.runner as runner_module

    result = StepResult(
        status="failed",
        output={
            "decision": "REJECT",
            "passed": False,
            "reason": "回归验证不足",
            "retry_reason": "回归验证不足",
            "retry_target": "run_regression",
        },
        failure=StepFailure(code="rejected", reason="人工打回：回归验证不足", recoverable=False),
        next_action="finalize_execution",
        audit=StepAudit(input_summary={}, output_summary={}, elapsed_ms=1),
    )
    monkeypatch.setattr(runner_module, "run_stage", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(runner_module, "cancellation_requested", lambda *_args, **_kwargs: False)

    spec = SimpleNamespace(
        name="request_approval",
        task_level=True,
        input_record=None,
        output_record=None,
        next_step="finalize_execution",
        patch=lambda output: {
            "decision": output["decision"],
            "approval_cleanup_failed": bool(output.get("approval_cleanup_failed")),
        },
    )
    store = SimpleNamespace(root=Path("/tmp"))
    node = runner_module.bind(spec, store, services=SimpleNamespace())

    state = node({"run_id": "run-id", "task_id": "task-id"})

    assert state["decision"] == "REJECT"
    assert state["retry_target"] == "run_regression"
    assert state["retry_reason"] == "回归验证不足"
    assert state["last_result"] == "failed"
