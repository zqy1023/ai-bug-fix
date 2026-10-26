"""在根因定位前创建任务级隔离工作区，Agent 只读取副本。"""

from __future__ import annotations

from typing import Any

from app.workflow.spec import StepSpec
from app.workflow.steps.prepare_workspace.models import WorkspaceInput, WorkspaceOutput
from app.workflow.steps.prepare_workspace.node import run


def _input(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": str(state.get("run_id") or ""),
        "task_id": str(state.get("task_id") or ""),
        "source_project_path": str(state.get("source_project_path") or ""),
        "baseline_dir": str(state.get("baseline_dir") or ""),
        "carrier": str((state.get("task") or {}).get("carrier") or ""),
        "framework": str(state.get("framework") or ""),
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    # 步骤没有产出工作区（被取消或执行失败）：不回写任何状态。
    if "ai_workspace_path" not in output:
        return {}
    return {"ai_workspace_path": str(output.get("ai_workspace_path") or "")}


SPEC = StepSpec(
    name="prepare_workspace",
    title="任务隔离工作区",
    runner=run,
    input_model=WorkspaceInput,
    output_model=WorkspaceOutput,
    input_builder=_input,
    output_patch=_patch,
)

__all__ = ["SPEC"]
