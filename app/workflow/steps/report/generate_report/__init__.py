"""交付前统一生成报告，分析报告和修复报告共用一个阶段。"""
from pathlib import Path
from app.workflow.spec import FrozenModel, StepSpec
from app.workflow.steps.report.finalize_execution import _input
from app.workflow.steps.report.finalize_execution.models import FinalizeInput
from app.workflow.steps.report.finalize_execution.node import build_report

class ReportOutput(FrozenModel):
    report_path: str
    summary: str


def run(ctx, data):
    import json
    report = build_report(ctx, data)
    report['report_kind'] = 'task_report'
    report['finished_at'] = None
    ctx.emit_artifact('task_report', 'reports/task.json', report)
    if data.report_destination:
        root = Path(data.ai_workspace_path).resolve()
        target = (root / data.report_destination).resolve()
        if not target.is_relative_to(root):
            raise ValueError('报告路径越界')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return ReportOutput(report_path='reports/task.json', summary='分析报告已生成，未修改业务代码' if data.report_destination else '修复与审查报告已生成')

SPEC = StepSpec(name='generate_report', title='生成报告', runner=run, input_model=FinalizeInput, output_model=ReportOutput, input_builder=_input, output_patch=lambda out: {'analysis_report': out, 'last_result': 'passed'})

