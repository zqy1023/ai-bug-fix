from __future__ import annotations

from app.workflow.routes import FINALIZE, RETRY, after_approval, after_check_changes


def test_validation_failure_uses_bounded_retry_node() -> None:
    assert after_check_changes({"last_result": "failed", "attempt": 1, "max_attempts": 3}) == RETRY
    assert after_check_changes({"last_result": "failed", "attempt": 3, "max_attempts": 3}) == FINALIZE


def test_human_rejection_cannot_continue_to_delivery() -> None:
    state = {"last_result": "failed", "decision": "REJECT", "attempt": 1, "max_attempts": 3}
    assert after_approval(state) == RETRY
    state["attempt"] = 3
    assert after_approval(state) == FINALIZE
