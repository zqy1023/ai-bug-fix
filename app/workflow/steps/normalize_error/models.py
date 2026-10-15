"""规范异常日志步骤的输入输出模型。"""

from __future__ import annotations


from pydantic import Field

from app.workflow.spec import FrozenModel


class NormalizeErrorInput(FrozenModel):
    """只从当前真实执行记录的查询条件读取日志。"""

    run_id: str = Field(min_length=1)


class NormalizedLog(FrozenModel):
    """一条标准化记录，保留错误原文和源数组位置。"""

    source_index: int = Field(ge=0)
    service_name: str = Field(min_length=1)
    biztype: str = Field(min_length=1)
    carrier: str = Field(min_length=1)
    req: str = Field(min_length=1)
    response: str = Field(default="")  # 可选，用于 response 重放验证
    track_config: str = Field(default="")  # 可选，用于 response 重放验证
    raw_error: str
    normalized_error: str = Field(min_length=1)


class NormalizeErrorOutput(FrozenModel):
    """仅记录处理计数。代表问题写入 run 级产物 ``artifacts/normalized_records.json``。"""

    run_id: str = Field(min_length=1)
    record_count: int = Field(ge=0)
    es_hits: int = Field(ge=0)
    unparseable: int = Field(ge=0)
    ignored_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    unique_count: int = Field(ge=0)


__all__ = ["NormalizeErrorInput", "NormalizeErrorOutput", "NormalizedLog"]
