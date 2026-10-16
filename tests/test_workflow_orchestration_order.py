from __future__ import annotations

from app.workflow.definitions import TASK_CONDITIONAL_TRANSITIONS, TASK_STATIC_TRANSITIONS
from app.workflow.routes import CLAIM, EVIDENCE, FINALIZE, LOCATE, SNAPSHOT, WORKSPACE


def test_task_flow_has_one_authoritative_topology() -> None:
    static = set(TASK_STATIC_TRANSITIONS)
    conditional = {source: (router, tuple(targets)) for source, router, targets in TASK_CONDITIONAL_TRANSITIONS}

    assert (SNAPSHOT, EVIDENCE) not in static
    assert conditional[SNAPSHOT][1] == (EVIDENCE, FINALIZE)
    assert conditional[EVIDENCE][1] == (LOCATE, FINALIZE)
    assert conditional[LOCATE][1] == (CLAIM, FINALIZE)
    assert conditional[CLAIM][1] == (WORKSPACE, FINALIZE)


def test_location_and_claim_routes_enforce_the_repair_gate() -> None:
    from app.workflow.routes import after_claim, after_locate

    assert after_locate({"last_result": "passed", "locate": {"status": "LOCATED"}}) == CLAIM
    assert after_locate({"last_result": "passed", "locate": {"status": "INSUFFICIENT_EVIDENCE"}}) == FINALIZE
    assert after_claim({"last_result": "passed"}) == WORKSPACE
    assert after_claim({"last_result": "failed"}) == FINALIZE
