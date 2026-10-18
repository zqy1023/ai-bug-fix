from __future__ import annotations

from app.workflow.routes import APPROVE, CLAIM, FINALIZE, LOCATE, REPAIR, RETRY, after_approval, after_check_changes, after_locate, after_repair, after_retry


def test_validation_failure_uses_bounded_retry_node() -> None:
    assert after_check_changes({"last_result": "failed", "attempt": 1, "max_attempts": 3}) == RETRY
    assert after_check_changes({"last_result": "failed", "attempt": 3, "max_attempts": 3}) == FINALIZE



def test_agent_failure_preserves_retry_target_and_retries_same_agent() -> None:
    from app.application.contracts import StepAudit, StepFailure, StepResult
    from app.workflow.runner import _failure_route_patch

    result = StepResult(
        status="failed",
        output={
            "retry_target": "locate_agent",
            "failure_reason": "AuthorizationException: HTTP 403",
        },
        failure=StepFailure(
            code="failed",
            reason="locate_agent 执行异常: AuthorizationException: HTTP 403",
            recoverable=True,
        ),
        audit=StepAudit(input_summary={}, output_summary={}, elapsed_ms=1),
    )

    patch = _failure_route_patch(result)
    assert patch["retry_target"] == "locate_agent"
    assert "HTTP 403" in patch["retry_reason"]
    assert after_retry({"retry_target": patch["retry_target"], "attempt": 1}) == LOCATE


def test_non_agent_failure_cannot_inject_retry_target() -> None:
    from app.application.contracts import StepAudit, StepFailure, StepResult
    from app.workflow.runner import _failure_route_patch

    result = StepResult(
        status="failed",
        output={"retry_target": "delete_repository"},
        failure=StepFailure(code="failed", reason="failed", recoverable=True),
        audit=StepAudit(input_summary={}, output_summary={}, elapsed_ms=1),
    )

    assert _failure_route_patch(result) == {}


def test_human_rejection_cannot_continue_to_delivery() -> None:
    state = {"last_result": "failed", "decision": "REJECT", "attempt": 1, "max_attempts": 3}
    assert after_approval(state) == RETRY
    state["attempt"] = 3
    assert after_approval(state) == FINALIZE



def test_missing_shared_layer_plan_routes_to_human_intervention():
    state = {
        "scope_escalation_required": True,
        "manual_intervention_required": True,
        "last_result": "manual",
        "attempt": 1,
        "max_attempts": 3,
    }
    assert after_repair(state) == APPROVE


def test_manual_continue_resumes_the_correct_stage_without_duplicate_claim():
    state = {
        "approval_mode": "manual_intervention",
        "decision": "CONTINUE_REPAIR",
        "manual_continue_allowed": True,
        "manual_resume_step": "repair_agent",
        "last_result": "passed",
    }
    assert after_approval(state) == REPAIR

    state["manual_resume_step"] = ""
    assert after_approval(state) == CLAIM

    state["repair_slot_claimed"] = True
    assert after_approval(state) == REPAIR

    state["decision"] = "RETRY_LOCATE"
    assert after_approval(state) == LOCATE


def test_manual_decision_does_not_leak_into_the_next_approval_gate():
    from app.workflow.steps.request_approval import _input

    state = {
        "decision": "CONTINUE_REPAIR",
        "manual_intervention_required": False,
        "manual_resume_step": "repair_agent",
        "task_id": "task-1",
        "task": {"carrier": "ExampleCarrier"},
        "locate": {"status": "LOCATED", "recommended_scope": "project"},
    }
    approval_input = _input(state)

    assert approval_input["decision"] == "PENDING"
    assert approval_input["mode"] == "change_approval"


def test_manual_continue_grants_only_the_explicit_repair_capability():
    from app.workflow.steps.request_approval import _patch

    continued = _patch({"decision": "CONTINUE_REPAIR"})
    retried = _patch({"decision": "RETRY_LOCATE"})

    assert continued["manual_continue_authorized"] is True
    assert retried["manual_continue_authorized"] is False



def test_retry_locate_does_not_claim_the_same_repair_slot_twice():
    state = {"locate": {"status": "LOCATED"}, "last_result": "passed"}
    assert after_locate(state) == CLAIM

    state["repair_slot_claimed"] = True
    assert after_locate(state) == REPAIR
