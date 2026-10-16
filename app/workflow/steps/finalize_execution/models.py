"""13 流程报告的输入输出契约。"""

from __future__ import annotations

from app.workflow.spec import FrozenModel


class FinalizeInput(FrozenModel):
    """12 的输入：从 steps.json 汇总本任务。"""

    # task_id：字段说明见该模型文档与工作流契约。
    task_id: str
    # carrier：字段说明见该模型文档与工作流契约。
    carrier: str
    # biztype：字段说明见该模型文档与工作流契约。
    biztype: str
    # terminal_status：字段说明见该模型文档与工作流契约。
    terminal_status: str


class FinalizeOutput(FrozenModel):
    """12 的输出。"""

    # report_path：字段说明见该模型文档与工作流契约。
    report_path: str = ""
    # delivery_status：任务最终业务结论。
    delivery_status: str = ""
    # terminal_step：触发任务结束的最后一个关键步骤。
    terminal_step: str = ""
    # terminal_reason：面向人的终止原因，所有终态必须提供。
    terminal_reason: str = ""
    # next_action：人工可执行的下一步建议。
    next_action: str = ""
    # finished_at：终态报告生成时间。
    finished_at: str = ""


__all__ = ["FinalizeInput", "FinalizeOutput"]
