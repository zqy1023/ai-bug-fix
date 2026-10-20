"""写终态报告并收尾。"""

from __future__ import annotations

from typing import Any

from app.workflow.spec import StepSpec
from app.workflow.steps.finalize_execution.models import FinalizeInput, FinalizeOutput
from app.workflow.steps.finalize_execution.node import run


def _input(state: dict[str, Any]) -> dict[str, Any]:
    from app.infrastructure.filesystem.workspace import WorkspaceManager

    task = dict(state.get("task") or {})
    evidence = dict(state.get("evidence") or {})
    carrier = WorkspaceManager.safe_carrier_name(str(task.get("carrier") or ""))
    locate = dict(state.get("locate") or {})
    legacy_reference = any(
        str(item.get("path") or "").replace("\\", "/").startswith("spiders/")
        for item in locate.get("source_evidence") or []
        if isinstance(item, dict)
    ) or any(
        str(item).replace("\\", "/").startswith("spiders/")
        for item in locate.get("candidate_files") or []
    )
    report_framework = (
        "spiders"
        if evidence.get("legacy_only") or evidence.get("legacy_dependency") or legacy_reference
        else str(state.get("framework") or "gathers")
    )
    # 优先使用证据中的真实 carrier 目录（保留源码大小写）；没有证据时才回退到
    # safe_carrier_name。Linux 上两者不同会写出一个模型从未修改过的目录。
    report_dir = str(evidence.get("carrier_dir") or "").replace("\\", "/").strip("/") or (
        f"{report_framework}/{carrier}"
    )
    return {
        "task_id": str(state.get("task_id") or ""),
        "carrier": str(task.get("carrier") or ""),
        "biztype": str(task.get("biztype") or ""),
        # human 模式同样会交付分支并走人工审批，终态必须按真实记录推导，
        # 不能再一律记成 manual，否则有提交的任务会被显示成"转人工报告"。
        "terminal_status": str(state.get("terminal_status") or ""),
        "ai_workspace_path": str(state.get("ai_workspace_path") or ""),
        "report_destination": f"{report_dir}/final.json",
        "retry_reason": str(state.get("retry_reason") or ""),
        "retry_target": str(state.get("retry_target") or ""),
        "blocked_reason": str(state.get("blocked_reason") or ""),
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    """把终态结论交回父图，作为本轮任务的结果汇总条目。"""
    return {"task_summary": dict(output)}


SPEC = StepSpec(
    name="finalize_execution",
    title="流程报告",
    runner=run,
    input_model=FinalizeInput,
    output_model=FinalizeOutput,
    input_builder=_input,
    output_patch=_patch,
)

__all__ = ["SPEC"]
