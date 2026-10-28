"""写终态报告并收尾。"""

from __future__ import annotations

from typing import Any

from core.contracts import StepSpec
from .models import FinalizeInput, FinalizeOutput
from .node import run


def _input(state: dict[str, Any]) -> dict[str, Any]:
    from infrastructure.workspace.workspace import WorkspaceManager

    task = dict(state.get("task") or {})
    evidence = dict(state.get("evidence") or {})
    carrier = WorkspaceManager.safe_carrier_name(str(task.get("carrier") or ""))
    locate = dict(state.get("locate") or {})
    # 旧框架判定与 prepare_delivery 共用同一实现，避免口径漂移。
    from workflow.stages.generate_report.report_paths import report_dir, report_framework

    report_framework_name = report_framework(evidence, locate, state.get("framework"))
    # 优先使用证据中的真实 carrier 目录（保留源码大小写）；没有证据时才回退到
    # safe_carrier_name。Linux 上两者不同会写出一个模型从未修改过的目录。
    report_directory = report_dir(evidence, report_framework_name, carrier)
    # 只有明确的分析报告任务需要把 final.json 复制到 AI 工作区供交付；
    # 修复任务若也写入该路径，重试 check_changes 时会被误认为源码新增文件。
    from workflow.engine.routes import _analysis_only_task

    report_destination = (
        f"{report_directory}/final.json" if _analysis_only_task(state) else ""
    )
    return {
        "task_id": str(state.get("task_id") or ""),
        "carrier": str(task.get("carrier") or ""),
        "biztype": str(task.get("biztype") or ""),
        # human 模式同样会交付分支并走人工审批，终态必须按真实记录推导，
        # 不能再一律记成 manual，否则有提交的任务会被显示成"转人工报告"。
        "terminal_status": str(state.get("terminal_status") or ""),
        "ai_workspace_path": str(state.get("ai_workspace_path") or ""),
        "report_destination": report_destination,
        "retry_reason": str(state.get("retry_reason") or ""),
        "retry_target": str(state.get("retry_target") or ""),
        "blocked_reason": str(state.get("blocked_reason") or ""),
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    """把终态结论交回父图，作为本轮任务的结果汇总条目。"""
    return {"task_summary": dict(output), "resolution": output.get("resolution")}


SPEC = StepSpec(
    name="finalize_execution",
    title="收尾汇总",
    runner=run,
    input_model=FinalizeInput,
    output_model=FinalizeOutput,
    input_builder=_input,
    output_patch=_patch,
)

__all__ = ["SPEC"]

