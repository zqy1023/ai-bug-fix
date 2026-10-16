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
    # delivery_status：字段说明见该模型文档与工作流契约。
    delivery_status: str = ""


__all__ = ["FinalizeInput", "FinalizeOutput"]
