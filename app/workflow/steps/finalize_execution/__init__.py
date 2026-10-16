"""写终态报告并收尾。"""

from __future__ import annotations

from typing import Any

from app.workflow.spec import Dependency, StepSpec
from app.workflow.steps.finalize_execution.models import FinalizeInput, FinalizeOutput
from app.workflow.steps.finalize_execution.node import run


def _input(state: dict[str, Any]) -> dict[str, Any]:
    from app.infrastructure.filesystem.workspace import WorkspaceManager

    task = dict(state.get("task") or {})
    evidence = dict(state.get("evidence") or {})
    carrier = WorkspaceManager.safe_carrier_name(str(task.get("carrier") or ""))
    report_framework = (
        "spiders"
        if evidence.get("legacy_only") or evidence.get("legacy_dependency")
        else str(state.get("framework") or "gathers")
    )
    return {
        "task_id": str(state.get("task_id") or ""),
        "carrier": str(task.get("carrier") or ""),
        "biztype": str(task.get("biztype") or ""),
        "terminal_status": str(state.get("terminal_status") or ""),
        "ai_workspace_path": str(state.get("ai_workspace_path") or ""),
        "report_destination": f"{report_framework}/{carrier}/final.json",
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    """把终态结论交回父图，作为本轮任务的结果汇总条目。"""
    return {"task_summary": dict(output)}


SPEC = StepSpec(
    name="finalize_execution",
    title="流程报告",
    runner=run,
    input_model=FinalizeInput,
    output_model=FinalizeOutput,
    input_builder=_input,
    output_patch=_patch,
    depends_on=(Dependency("claim_repair_slot", "task"),),
)

__all__ = ["SPEC"]
