"""在独立 Git 工作区执行准生产回归。"""

from __future__ import annotations

from typing import Any

from app.workflow.spec import StepSpec
from app.agents.review.tools.run_regression.models import RegressionInput, RegressionOutput
from app.agents.review.tools.run_regression.node import run


def _input(state: dict[str, Any]) -> dict[str, Any]:
    task = dict(state.get("task") or {})
    return {
        "previous_result": dict(state.get("regression_result") or {}),
        "task_id": str(state.get("task_id") or ""),
        "carrier": str(task.get("carrier") or ""),
        "ai_workspace_path": str(state.get("ai_workspace_path") or ""),
        "changed_files": [str(item) for item in state.get("changed_files") or []],
        "business_verification_status": str(
            (state.get("verify_result") or {}).get("status", "unverified")
        ),
        "normalized_log": dict(state.get("problem") or {}),
        "baseline_dir": str(state.get("baseline_dir") or ""),
        "source_project_path": str(state.get("source_project_path") or ""),
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    result = {"mode": "regression", **dict(output)}
    status = str(result.get("status") or "").lower()
    if "passed" not in output:
        # 工具被取消或异常中断：没有结论可写。
        return {}
    return {
        "regression_result": result,
        # 未验证结果交由统一决策重试或阻断，不授予交付资格。
        "last_result": "passed"
        if status in {"unverified", "skipped", "not_configured", "not_executed"}
        or result.get("passed")
        else "failed",
    }


SPEC = StepSpec(
    name="run_regression",
    title="业务回归命令",
    runner=run,
    input_model=RegressionInput,
    output_model=RegressionOutput,
    input_builder=_input,
    output_patch=_patch,
)

__all__ = ["SPEC"]
