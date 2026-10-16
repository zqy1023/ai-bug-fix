"""模型调用前的确定性任务预分析。"""

from __future__ import annotations

from typing import Any

from app.workflow.spec import Dependency, RecordView, StepSpec
from app.workflow.steps.prepare_evidence.models import EvidenceInput, EvidenceOutput
from app.workflow.steps.prepare_evidence.node import run


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
    """预检结论回写状态；纯旧框架仍需 Locate 分析，但不进入修复。"""
    patch: dict[str, Any] = {"evidence": output}
    if output.get("legacy_only") or output.get("legacy_dependency"):
        patch["terminal_status"] = "manual"
    return patch


SPEC = StepSpec(
    name="prepare_evidence",
    title="准备定位证据",
    runner=run,
    input_model=EvidenceInput,
    output_model=EvidenceOutput,
    input_builder=_input,
    output_patch=_patch,
    depends_on=(Dependency("identify_carrier", "run"), Dependency("prepare_source_snapshot", "task"), Dependency("prepare_workspace", "task")),
    next_step="locate_agent",
    # 原始请求保存在去重日志产物中，记录中仅保留分类与错误证据。
    input_record=RecordView(
        trim=(("problem", ("carrier", "error", "service_name", "biztype")),)
    ),
)

__all__ = ["SPEC"]
