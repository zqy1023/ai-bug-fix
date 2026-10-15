"""领取 ``biztype + carrier`` 修复资格。"""

from __future__ import annotations

from typing import Any

from app.workflow.spec import Dependency, StepSpec
from app.workflow.steps.claim_repair_slot.models import ClaimInput, ClaimOutput
from app.workflow.steps.claim_repair_slot.node import run


def _input(state: dict[str, Any]) -> dict[str, Any]:
    task = dict(state.get("task") or {})
    return {
        "run_id": str(state.get("run_id") or ""),
        "task_id": str(state.get("task_id") or task.get("task_id") or ""),
        "biztype": str(task.get("biztype") or ""),
        "carrier": str(task.get("carrier") or ""),
        "source_repo_path": str(state.get("source_repo_path") or ""),
    }


SPEC = StepSpec(
    name="claim_repair_slot",
    title="领取资格",
    runner=run,
    input_model=ClaimInput,
    output_model=ClaimOutput,
    input_builder=_input,
    depends_on=(Dependency("identify_carrier", "run"),),
    next_step="prepare_source_snapshot",
)

__all__ = ["SPEC"]
