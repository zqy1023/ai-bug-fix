"""规范异常日志步骤的输入输出模型。"""

from __future__ import annotations


from pydantic import Field

from core.contracts import FrozenModel


class NormalizeErrorInput(FrozenModel):
    """只从当前真实执行记录的查询条件读取日志。"""

    # run_id：字段说明见该模型文档与工作流契约。
    run_id: str = Field(min_length=1)


class NormalizedLog(FrozenModel):
    """一条标准化记录，保留错误原文和源数组位置。"""

    # source_index：字段说明见该模型文档与工作流契约。
    source_index: int = Field(ge=0)
    # service_name：字段说明见该模型文档与工作流契约。
    service_name: str = Field(min_length=1)
    # biztype：字段说明见该模型文档与工作流契约。
    biztype: str = Field(min_length=1)
    # carrier：字段说明见该模型文档与工作流契约。
    carrier: str = Field(min_length=1)
    # req：字段说明见该模型文档与工作流契约。
    req: str = Field(min_length=1)
    response: str = Field(default="")  # 可选，用于 response 重放验证
    track_config: str = Field(default="")  # 可选，用于 response 重放验证
    # raw_error：字段说明见该模型文档与工作流契约。
    raw_error: str
    message: str = ""
    # normalized_error：字段说明见该模型文档与工作流契约。
    normalized_error: str = Field(min_length=1)


class NormalizeErrorOutput(FrozenModel):
    """仅记录处理计数。代表问题写入 run 级产物 ``artifacts/normalized_records.json``。"""

    # run_id：字段说明见该模型文档与工作流契约。
    run_id: str = Field(min_length=1)
    # record_count：字段说明见该模型文档与工作流契约。
    record_count: int = Field(ge=0)
    # es_hits：字段说明见该模型文档与工作流契约。
    es_hits: int = Field(ge=0)
    # unparseable：字段说明见该模型文档与工作流契约。
    unparseable: int = Field(ge=0)
    # ignored_count：字段说明见该模型文档与工作流契约。
    ignored_count: int = Field(ge=0)
    # duplicate_count：字段说明见该模型文档与工作流契约。
    duplicate_count: int = Field(ge=0)
    # unique_count：字段说明见该模型文档与工作流契约。
    unique_count: int = Field(ge=0)
    record_limit: int = 0
    truncated: bool = False


__all__ = ["NormalizeErrorInput", "NormalizeErrorOutput", "NormalizedLog"]
