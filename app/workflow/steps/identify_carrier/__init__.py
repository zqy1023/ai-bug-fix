"""校验异常并创建任务，一次性生成全部执行节点。"""

from __future__ import annotations

from typing import Any

from app.workflow.spec import Dependency, RecordView, StepSpec
from app.workflow.steps.identify_carrier.models import IdentifyCarrierInput, IdentifyCarrierOutput
from app.workflow.steps.identify_carrier.node import run


def _input(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": str(state.get("run_id") or ""),
        "task_filter_carrier": str(state.get("task_filter_carrier") or ""),
        "task_filter_biztype": str(state.get("task_filter_biztype") or ""),
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    return {"tasks": list(output["tasks"])}


SPEC = StepSpec(
    name="identify_carrier",
    title="识别运输商任务",
    runner=run,
    input_model=IdentifyCarrierInput,
    output_model=IdentifyCarrierOutput,
    input_builder=_input,
    output_patch=_patch,
    task_level=False,
    depends_on=(Dependency("deduplicate_error", "run"),),
    next_step="task_dispatch",
    # 步骤记录只存分类字段和错误摘要；代表请求留在 run 级 normalized_records 产物里。
    output_record=RecordView(
        trim=(
            (
                "tasks",
                ("task_id", "biztype", "carrier", "service_name", "problem_key", "problem"),
            ),
            ("tasks.problem", ("carrier", "error", "service_name", "biztype")),
        )
    ),
)

__all__ = ["SPEC"]
