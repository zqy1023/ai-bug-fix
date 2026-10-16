from __future__ import annotations

from app.workflow.definitions import (
    PHASE_COMPOSITIONS,
    TASK_CONDITIONAL_TRANSITIONS,
    TASK_NODE_NAMES,
)
from app.workflow.routes import (
    CLAIM,
    FINALIZE,
    LOCATE,
    PREPARE_CONTEXT,
    REGRESSION,
    RUN_PREPARE,
    VALIDATE,
    WORKSPACE,
)


def test_task_flow_uses_compact_phase_nodes() -> None:
    conditional = {
        source: (router, tuple(targets))
        for source, router, targets in TASK_CONDITIONAL_TRANSITIONS
    }

    assert len([name for name in TASK_NODE_NAMES if name != "retry_prepare"]) == 12
    assert PHASE_COMPOSITIONS[RUN_PREPARE] == (
        "discover_logs",
        "normalize_error",
        "deduplicate_error",
    )
    assert PHASE_COMPOSITIONS[PREPARE_CONTEXT] == (
        "prepare_source_snapshot",
        "prepare_evidence",
    )
    assert PHASE_COMPOSITIONS[VALIDATE] == ("check_changes", "verify_issue")
    assert conditional[PREPARE_CONTEXT][1] == (LOCATE, FINALIZE)
    assert conditional[LOCATE][1] == (CLAIM, FINALIZE)
    assert conditional[CLAIM][1] == (WORKSPACE, FINALIZE)
    assert conditional[VALIDATE][1] == (REGRESSION, "retry_prepare", FINALIZE)


def test_location_and_claim_routes_enforce_the_repair_gate() -> None:
    from app.workflow.routes import after_claim, after_locate

    assert after_locate({"last_result": "passed", "locate": {"status": "LOCATED"}}) == CLAIM
    assert after_locate({"last_result": "passed", "locate": {"status": "INSUFFICIENT_EVIDENCE"}}) == FINALIZE
    assert after_claim({"last_result": "passed"}) == WORKSPACE
    assert after_claim({"last_result": "failed"}) == FINALIZE


def test_context_and_validation_phases_stop_at_the_gate() -> None:
    from app.workflow.routes import after_prepare_context, after_validate_changes

    assert after_prepare_context({"last_result": "passed", "evidence": {"legacy_only": False}}) == LOCATE
    assert after_prepare_context({"last_result": "failed", "evidence": {}}) == FINALIZE
    assert after_prepare_context({"last_result": "passed", "evidence": {"legacy_only": True}}) == FINALIZE
    assert after_validate_changes({"last_result": "passed"}) == REGRESSION
    assert after_validate_changes({"last_result": "failed", "attempt": 3, "max_attempts": 3}) == FINALIZE


def test_manual_resolution_finishes_current_task_so_parent_can_dispatch_next():
    from app.workflow.routes import after_approval
    from app.workflow.graph import RUN_TASK, after_dispatch, collect
    from app.workflow.definitions import TASK_STATIC_TRANSITIONS

    assert after_approval({
        "approval_mode": "manual_intervention",
        "decision": "MARK_DONE",
        "last_result": "passed",
    }) == FINALIZE
    assert after_approval({
        "approval_mode": "manual_intervention",
        "decision": "TERMINATE",
        "last_result": "passed",
    }) == FINALIZE
    assert (FINALIZE, "__end__") in TASK_STATIC_TRANSITIONS

    state = {
        "task_cursor": 0,
        "task": {"task_id": "task-1", "carrier": "A", "biztype": "track"},
        "task_summary": {"task_id": "task-1", "result": "MANUAL"},
        "tasks": [{"task_id": "task-1"}, {"task_id": "task-2"}],
    }
    collected = collect(state)
    assert collected["task_cursor"] == 1
    assert after_dispatch({**state, **collected}) == RUN_TASK


def test_shared_layer_change_plan_reaches_approval_then_apply_plan():
    from app.workflow.routes import APPLY, APPROVE, after_approval, after_repair

    state = {
        "last_result": "passed",
        "scope_escalation_required": True,
        "change_plan": {"scope": "project", "edits": [{"path": "common/redis_utils.py"}]},
    }

    assert after_repair(state) == APPROVE
    assert after_approval({
        **state,
        "approval_mode": "change_approval",
        "decision": "APPROVE",
        "last_result": "passed",
    }) == APPLY

