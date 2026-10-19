from __future__ import annotations

from app.workflow.routes import APPROVE, CLAIM, FINALIZE, LOCATE, REPAIR, RETRY, after_approval, after_validate_changes, after_locate, after_repair, after_retry


def test_validation_failure_uses_bounded_retry_node() -> None:
    assert after_validate_changes({"last_result": "failed", "attempt": 1, "max_attempts": 3}) == RETRY
    assert after_validate_changes({"last_result": "failed", "attempt": 3, "max_attempts": 3}) == FINALIZE



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



def test_scope_escalation_stops_before_repair_validation_or_approval():
    state = {"scope_escalation_required": True, "last_result": "manual"}
    assert after_repair(state) == FINALIZE
