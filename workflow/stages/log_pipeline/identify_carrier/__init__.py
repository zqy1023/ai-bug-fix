"""校验异常并创建任务，一次性生成全部执行节点。"""

from __future__ import annotations

from typing import Any

from core.contracts import RecordView, StepSpec
from .models import (
    IdentifyCarrierInput,
    IdentifyCarrierOutput,
)
from .node import run


def _input(state: dict[str, Any]) -> dict[str, Any]:
    return {"run_id": str(state.get("run_id") or "")}


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    # 步骤没有产出任务列表（被取消或执行失败）：不回写任何状态。
    if "tasks" not in output:
        return {}
    return {"tasks": list(output.get("tasks") or [])}


SPEC = StepSpec(
    name="identify_carrier",
    title="识别运输商任务",
    runner=run,
    input_model=IdentifyCarrierInput,
    output_model=IdentifyCarrierOutput,
    input_builder=_input,
    output_patch=_patch,
    task_level=False,
    # 步骤记录只存分类字段和错误摘要；代表请求留在 run 级 normalized_records 产物里。
    # raw_error / message 是原始日志原文，必须保留：审批与任务详情都要用完整日志判断问题，
    # 只留规范化后的 error 会把堆栈和上下文丢掉。
    #
    # req / response 同样不能裁：终态报告与交付分支上的 task_summary.md 都要展示
    # 完整原始异常——ApexMotor 那轮根因就在 response 的 "PRO=" 前缀上，缺了它
    # 交付文档只剩一句"上游响应：未捕获"，复核者无法判断修复对不对。
    output_record=RecordView(
        trim=(
            (
                "tasks",
                (
                    "task_id",
                    "biztype",
                    "carrier",
                    "service_name",
                    "problem_key",
                    "problem",
                ),
            ),
            (
                "tasks.problem",
                (
                    "carrier",
                    "error",
                    "raw_error",
                    "message",
                    "service_name",
                    "biztype",
                    "req",
                    "response",
                    "original_exception_message",
                    "related_messages",
                ),
            ),
        )
    ),
)

__all__ = ["SPEC"]

