"""过滤、去重步骤的输入输出模型。"""

from __future__ import annotations

from pydantic import Field

from app.workflow.spec import FrozenModel


class DeduplicateErrorInput(FrozenModel):
    """只从当前执行的 normalize_error 步骤记录读取。"""

    run_id: str = Field(min_length=1)


class DeduplicatedLog(FrozenModel):
    """一个稳定错误键的代表问题。来源索引只保留代表条，避免 2 万条下标常驻内存。"""

    dedup_key: str = Field(min_length=1)
    service_name: str = Field(min_length=1)
    biztype: str = Field(min_length=1)
    carrier: str = Field(min_length=1)
    error: str = Field(min_length=1)
    representative_index: int = Field(ge=0)
    occurrence_count: int = Field(ge=1)
    problem: dict[str, str]


class DeduplicateErrorOutput(FrozenModel):
    """去重统计。代表问题在 normalize_error 的步骤记录里。"""

    run_id: str = Field(min_length=1)
    input_count: int = Field(ge=0)
    ignored_count: int = Field(ge=0)
    unique_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)


__all__ = ["DeduplicateErrorInput", "DeduplicateErrorOutput", "DeduplicatedLog"]
