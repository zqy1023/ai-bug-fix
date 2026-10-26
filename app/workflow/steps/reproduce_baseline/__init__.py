"""修改前复现原始异常。"""

from typing import Any

from app.domain.errors import StepFailed
from app.workflow.acceptance import ReproductionInput, reproduce
from app.workflow.contracts import BaselineReproduction
from app.workflow.spec import RecordView, StepSpec

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


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    # 步骤没有产出复现结论（被取消或执行失败）：不回写任何状态。
    if "status" not in output:
        return {}
    passed = str(output.get("status") or "") == "passed"
    return {
        "baseline_reproduction": output,
        "retry_target": "reproduce_baseline" if output.get("retryable") else "",
        "last_result": "passed" if passed else "manual",
        # 基线未建立表示“无法验证”，不是流程阻断。保留原因并交给分析报告
        # 分支，由人工按根因和建议验收标准处理。
        "terminal_status": "passed" if passed else "",
        "blocked_reason": "" if passed else str(output.get("reason") or ""),
    }


SPEC = StepSpec(
    name="reproduce_baseline",
    title="修改前复现原始异常",
    runner=reproduce,
    input_model=ReproductionInput,
    output_model=BaselineReproduction,
    input_builder=input_of,
    output_patch=_patch,
    # 落盘投影：baseline_manifest 是"carrier 全部文件 × 哈希"的全量清单，
    # 体积随 carrier 规模线性增长。它不进步骤记录，否则记录会顶破 1 MiB 上限，
    # 而写记录失败会连带让失败记录也写不进去，整轮中止。清单本体已经在
    # source_snapshot.json 产物与状态通道里各有一份。
    input_record=RecordView(
        keep=(
            "task_id",
            "problem",
            "baseline_dir",
            "source_project_path",
            "ai_workspace_path",
            "acceptance_contract",
            "carrier",
            "candidates",
        )
    ),
)
