"""13 流程报告的输入输出契约。"""

from __future__ import annotations

from app.workflow.spec import FrozenModel


class FinalizeInput(FrozenModel):
    """12 的输入：从 steps.json 汇总本任务。"""

    task_id: str
    carrier: str
    biztype: str
    terminal_status: str


class FinalizeOutput(FrozenModel):
    """12 的输出。"""

    report_path: str = ""
    delivery_status: str = ""


__all__ = ["FinalizeInput", "FinalizeOutput"]
