"""落盘结构化方案：整个工作流唯一写盘通道（原无独立步骤，新增）。"""

from __future__ import annotations

from typing import Any

from app.workflow.spec import Dependency, StepSpec
from app.workflow.steps.apply_plan.models import ApplyPlanInput, ApplyPlanOutput
from app.workflow.steps.apply_plan.node import run


def _input(state: dict[str, Any]) -> dict[str, Any]:
    task = dict(state.get("task") or {})
    return {
        "task_id": str(state.get("task_id") or ""),
        "carrier": str(task.get("carrier") or ""),
        "ai_workspace_path": str(state.get("ai_workspace_path") or ""),
        "decision": str(state.get("decision") or "PENDING"),
        "change_plan": state.get("change_plan") or None,
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    """落盘后把已授权范围写回主图，让后续门禁按同一范围校验。

    只追加本轮方案实际落盘的文件，不覆盖修复 Agent 已记录的 carrier 改动。
    """
    plan_files = [str(item) for item in output["written_files"]]
    return {
        "plan_applied": bool(output["applied"]),
        "plan_changed_files": plan_files,
        "authorized_files": [str(item) for item in output["authorized_files"]],
    }


SPEC = StepSpec(
    name="apply_plan",
    title="方案落盘",
    runner=run,
    input_model=ApplyPlanInput,
    output_model=ApplyPlanOutput,
    input_builder=_input,
    output_patch=_patch,
    requires_confirmation=True,
    depends_on=(Dependency("request_approval", "task"),),
    next_step="prepare_delivery",
)

__all__ = ["SPEC"]
