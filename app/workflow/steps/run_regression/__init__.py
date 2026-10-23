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
        "business_verification_status": str(state.get("business_verification_status") or "unknown"),
        "normalized_log": dict(state.get("problem") or {}),
        "baseline_dir": str(state.get("baseline_dir") or ""),
        "source_project_path": str(state.get("source_project_path") or ""),
        "validation_requirements": list((state.get("verify_result") or {}).get("validation_requirements") or []),
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    result = {"mode": "regression", **dict(output)}
    status = str(result.get("status") or "").lower()
    return {
        "regression_passed": bool(result["passed"]) and status == "verified",
        "regression_result": result,
        # 未验证/无配置命令是人工确认路径，允许审查和交付，不伪装成回归通过。
        "last_result": "passed" if status in {"unverified", "skipped", "not_configured", "not_executed"} or result["passed"] else "failed",
    }


SPEC = StepSpec(
    name="run_regression",
    title="业务回归命令",
    runner=run,
    input_model=RegressionInput,
    output_model=RegressionOutput,
    input_builder=_input,
    output_patch=_patch,
    depends_on=(Dependency("verify_issue", "task"),),
    next_step="review_agent",
)

__all__ = ["SPEC"]
