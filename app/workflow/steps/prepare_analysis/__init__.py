"""生成分析报告，不提前结束任务或释放资格。"""

import json
from pathlib import Path

from app.domain.errors import StepFailed
from app.workflow.spec import FrozenModel, StepSpec
from app.workflow.steps.finalize_execution import _input
from app.workflow.steps.finalize_execution.models import FinalizeInput
from app.workflow.steps.finalize_execution.node import build_report


class Output(FrozenModel):
    report_path: str
    reason: str
    next_action: str = ""


def run(ctx, data: FinalizeInput) -> Output:
    report = build_report(ctx, data)
    baseline = report.get("baseline_reproduction") or {}
    if str(baseline.get("status") or "").lower() == "unverified":
        reason = (
            "未能建立验收基线："
            f"{baseline.get('reason') or '原始证据不足'}；"
            "已生成分析报告，未执行代码修复。"
        )
    else:
        reason = str(report.get("terminal_reason") or "已生成分析报告。")
    next_action = "按分析报告中的建议验收标准人工修复并验证后，再重新发起任务。"
    report.update(
        report_kind="analysis_report",
        result="ANALYSIS_READY",
        status="ANALYSIS_READY",
        terminal_reason=reason,
        next_action=next_action,
        finished_at=None,
    )
    resolution = report.get("resolution")
    if isinstance(resolution, dict):
        resolution.update(
            reason=reason,
            next_action=next_action,
            terminal_step="prepare_analysis",
        )
    if not data.report_destination:
        raise StepFailed("缺少分析报告交付路径", code="ARTIFACT_PATH_INVALID")
    root = Path(data.ai_workspace_path).resolve()
    destination = (root / data.report_destination).resolve()
    if not destination.is_relative_to(root):
        # 路径越界是安全信号，必须带码落进记录，不能折叠成通用执行失败。
        raise StepFailed("分析报告路径越界", code="ARTIFACT_PATH_INVALID")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ctx.emit_artifact("analysis_report", "reports/analysis.json", report)
    return Output(
        report_path=data.report_destination,
        reason=reason,
        next_action=next_action,
    )


SPEC = StepSpec(
    name="prepare_analysis",
    title="生成分析报告",
    runner=run,
    input_model=FinalizeInput,
    output_model=Output,
    input_builder=_input,
    output_patch=lambda output: {"analysis_report": output, "last_result": "passed"},
)
