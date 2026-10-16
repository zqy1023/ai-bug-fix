from __future__ import annotations

from app.workflow.routes import (
    CLAIM,
    FINALIZE,
    LOCATE,
    WORKSPACE,
    after_claim,
    after_locate,
)


def test_locate_must_succeed_before_claiming_repair_slot() -> None:
    located = {
        "last_result": "passed",
        "terminal_status": "",
        "locate": {"status": "LOCATED"},
    }
    assert after_locate(located) == CLAIM

    insufficient = {
        "last_result": "passed",
        "terminal_status": "",
        "locate": {"status": "INSUFFICIENT_EVIDENCE"},
    }
    assert after_locate(insufficient) == FINALIZE

    manual = {
        "last_result": "passed",
        "terminal_status": "",
        "locate": {"status": "MANUAL"},
    }
    assert after_locate(manual) == FINALIZE


def test_claim_must_succeed_before_workspace_creation() -> None:
    assert after_claim({"last_result": "passed"}) == WORKSPACE
    assert after_claim({"last_result": "skipped"}) == FINALIZE
    assert after_claim({"last_result": "failed"}) == FINALIZE
