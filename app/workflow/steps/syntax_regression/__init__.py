"""语法回归：只负责代码可解析性，不判断业务行为。"""

from __future__ import annotations

from typing import Any

from app.workflow.spec import Dependency, StepSpec
from app.workflow.steps.syntax_regression.models import SyntaxInput, SyntaxOutput
from app.workflow.steps.syntax_regression.node import run


def _input(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "ai_workspace_path": str(state.get("ai_workspace_path") or ""),
        "changed_files": [str(item) for item in state.get("changed_files") or []],
        "baseline_manifest": dict(state.get("baseline_manifest") or {}),
        "deleted_files": [
            str(item.get("path")) for item in (state.get("gate_result") or {}).get("files", [])
            if isinstance(item, dict) and item.get("status") == "deleted" and item.get("in_scope") is True
        ],
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    return {"syntax_passed": bool(output["passed"]), "syntax_result": output}


SPEC = StepSpec(
    name="syntax_regression",
    title="语法回归",
    runner=run,
    input_model=SyntaxInput,
    output_model=SyntaxOutput,
    input_builder=_input,
    output_patch=_patch,
    depends_on=(Dependency("check_changes", "task"),),
    next_step="verify_issue",
)

__all__ = ["SPEC"]
