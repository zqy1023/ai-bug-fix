"""任务终态汇总的输入输出契约。"""

from __future__ import annotations

from core.contracts import TaskResolution
from core.contracts import FrozenModel


class FinalizeInput(FrozenModel):
    """收尾步骤的输入：从 steps.json 汇总本任务。"""

    # task_id：字段说明见该模型文档与工作流契约。
    task_id: str
    # carrier：字段说明见该模型文档与工作流契约。
    carrier: str
    # biztype：字段说明见该模型文档与工作流契约。
    biztype: str
    # terminal_status：字段说明见该模型文档与工作流契约。
    terminal_status: str
    # ai_workspace_path：用于为分析型任务准备只读最终报告分支。
    ai_workspace_path: str = ""
    # report_destination：分析型报告在隔离区中的目标相对路径。
    report_destination: str = "reports/final.json"
    # 重试节点的原因只存在于状态通道，没有 StepRecord；收尾时必须带上，
    # 否则最终报告只能写"未提供更具体的失败原因"。
    retry_reason: str = ""
    retry_target: str = ""
    # blocked_reason：repair_agent 给出的结构化阻塞原因。
    blocked_reason: str = ""


class FinalizeOutput(FrozenModel):
    resolution: TaskResolution | None = None
    """收尾步骤的输出。"""

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
