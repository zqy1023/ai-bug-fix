"""模型调用前的确定性任务预分析。"""

from __future__ import annotations

from typing import Any

from app.workflow.spec import RecordView, StepSpec
from app.workflow.steps.context.prepare_evidence.models import EvidenceInput, EvidenceOutput
from app.workflow.steps.context.prepare_evidence.node import run


def _input(state: dict[str, Any]) -> dict[str, Any]:
    task = dict(state.get("task") or {})
    return {
        "task_id": str(state.get("task_id") or ""),
        "carrier": str(task.get("carrier") or ""),
        "biztype": str(task.get("biztype") or ""),
        "service_name": str(task.get("service_name") or ""),
        "problem": dict(state.get("problem") or task.get("problem") or {}),
        "workspace_path": str(state.get("ai_workspace_path") or ""),
        "framework": str(state.get("framework") or ""),
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    """保留完整定位证据；旧框架仍执行只读定位，再统一转分析报告。"""
    return {"evidence": output}


SPEC = StepSpec(
    name="prepare_evidence",
    title="准备定位证据",
    runner=run,
    input_model=EvidenceInput,
    output_model=EvidenceOutput,
    input_builder=_input,
    output_patch=_patch,
    # 原始请求保存在去重日志产物中，记录中仅保留分类与错误证据。
    # raw_error / message 是原始日志原文，保留下来供任务详情与审批核对。
    input_record=RecordView(
        trim=(("problem", ("carrier", "error", "raw_error", "message", "service_name", "biztype")),)
    ),
)

__all__ = ["SPEC"]

