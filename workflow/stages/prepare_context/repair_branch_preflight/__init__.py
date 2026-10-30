"""在创建隔离工作区前只读检查未合并修复分支。"""

from __future__ import annotations

from typing import Any

from core.contracts import StepSpec
from .models import (
    PreflightInput,
    PreflightOutput,
)
from .node import run


def _input(state: dict[str, Any]) -> dict[str, Any]:
    task = dict(state.get("task") or {})
    return {
        "task_id": str(state.get("task_id") or task.get("task_id") or ""),
        "biztype": str(task.get("biztype") or ""),
        "carrier": str(task.get("carrier") or ""),
        "source_repo_path": str(state.get("source_repo_path") or ""),
        "problem_key": str(task.get("problem_key") or ""),
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    branch = str(output.get("repair_branch") or "")
    return {"repair_branch": branch} if branch else {}


SPEC = StepSpec(
    name="repair_branch_preflight",
    title="修复分支预检",
    runner=run,
    input_model=PreflightInput,
    output_model=PreflightOutput,
    input_builder=_input,
    output_patch=_patch,
)

__all__ = ["SPEC"]

