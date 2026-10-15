"""在独立 Git 工作区执行准生产回归。"""

from __future__ import annotations

from typing import Any

from app.workflow.spec import Dependency, StepSpec
from app.workflow.steps.run_regression.models import RegressionInput, RegressionOutput
from app.workflow.steps.run_regression.node import run


def _input(state: dict[str, Any]) -> dict[str, Any]:
    task = dict(state.get("task") or {})
    return {
        "task_id": str(state.get("task_id") or ""),
        "carrier": str(task.get("carrier") or ""),
        "ai_workspace_path": str(state.get("ai_workspace_path") or ""),
        "changed_files": [str(item) for item in state.get("changed_files") or []],
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    return {"regression_passed": bool(output["passed"]), "regression_result": output}


SPEC = StepSpec(
    name="run_regression",
    title="准生产回归",
    runner=run,
    input_model=RegressionInput,
    output_model=RegressionOutput,
    input_builder=_input,
    output_patch=_patch,
    requires_confirmation=True,
    depends_on=(Dependency("verify_issue", "task"),),
    next_step="review_agent",
)

__all__ = ["SPEC"]
