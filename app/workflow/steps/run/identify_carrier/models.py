"""03 创建任务的输入输出契约。"""

from __future__ import annotations

from typing import Any

from app.workflow.spec import FrozenModel
from pydantic import Field


class TaskRef(FrozenModel):
    """03 交给后续图的任务引用，不是整份任务状态。"""

    # task_id：字段说明见该模型文档与工作流契约。
    task_id: str
    # biztype：字段说明见该模型文档与工作流契约。
    biztype: str
    # carrier：字段说明见该模型文档与工作流契约。
    carrier: str
    # service_name：字段说明见该模型文档与工作流契约。
    service_name: str
    # problem_key：字段说明见该模型文档与工作流契约。
    problem_key: str
    # problem：字段说明见该模型文档与工作流契约。
    problem: dict[str, Any] = Field(default_factory=dict)


class IdentifyCarrierInput(FrozenModel):
    """按原始服务、业务类型和 carrier 识别本轮任务。"""

    # run_id：字段说明见该模型文档与工作流契约。
    run_id: str


class IdentifyCarrierOutput(FrozenModel):
    """按 ``(biztype, carrier)`` 建立任务及被排除记录的数量；每组只留一个代表异常。"""

    # tasks：字段说明见该模型文档与工作流契约。
    tasks: list[TaskRef] = Field(default_factory=list)
    # created：字段说明见该模型文档与工作流契约。
    created: int = 0
    # manual：字段说明见该模型文档与工作流契约。
    manual: int = 0


__all__ = ["IdentifyCarrierInput", "IdentifyCarrierOutput", "TaskRef"]
