"""修改前复现原始异常。"""

from typing import Any

from app.domain.errors import StepFailed
from app.workflow.acceptance import ReproductionInput, reproduce
from app.workflow.contracts import BaselineReproduction
from app.workflow.spec import StepSpec

_REQUIRED_KEYS = (
    "task_id",
    "problem",
    "baseline_dir",
    "baseline_manifest",
    "source_project_path",
    "ai_workspace_path",
    "acceptance_contract",
)


def input_of(state: dict[str, Any]) -> dict[str, Any]:
    missing = [key for key in _REQUIRED_KEYS if key not in state]
    if missing:
        raise StepFailed(
            f"复现上下文缺失: {missing[0]}", code="REPRODUCE_CONTEXT_MISSING"
        )
    task = state.get("task")
    locate = state.get("locate")
    if not isinstance(task, dict) or "carrier" not in task:
        raise StepFailed("复现上下文缺失: task.carrier", code="REPRODUCE_CONTEXT_MISSING")
    if not isinstance(locate, dict):
        raise StepFailed("复现上下文缺失: locate", code="REPRODUCE_CONTEXT_MISSING")
    return {
        key: state[key]
        for key in _REQUIRED_KEYS
    } | {
        "carrier": task["carrier"],
        "candidates": locate.get("candidate_files", []),
    }


SPEC = StepSpec(
    name="reproduce_baseline",
    title="修改前复现原始异常",
    runner=reproduce,
    input_model=ReproductionInput,
    output_model=BaselineReproduction,
    input_builder=input_of,
    output_patch=lambda output: {
        "baseline_reproduction": output,
        "retry_target": "reproduce_baseline" if output.get("retryable") else "",
        "last_result": "passed" if output["status"] == "passed" else "manual",
        "terminal_status": "passed" if output["status"] == "passed" else "BLOCKED",
        "blocked_reason": "" if output["status"] == "passed" else output["reason"],
    },
)
