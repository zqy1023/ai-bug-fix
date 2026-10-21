"""01 拉取日志的输入输出契约。"""

from __future__ import annotations

from app.workflow.spec import FrozenModel
from pydantic import Field, model_validator


class DiscoverInput(FrozenModel):
    """实际 ES 查询口径；不支持注入样本日志。"""

    # service_name：字段说明见该模型文档与工作流契约。
    service_name: str = Field(min_length=1)
    # service_prefix：字段说明见该模型文档与工作流契约。
    service_prefix: str = Field(min_length=1)
    # excluded_messages：字段说明见该模型文档与工作流契约。
    excluded_messages: list[str] = Field(default_factory=list)
    # excluded_biztypes：字段说明见该模型文档与工作流契约。
    excluded_biztypes: list[str] = Field(default_factory=list)
    # hours：精确滚动回溯小时数。
    hours: int = Field(ge=1)
    # limit：字段说明见该模型文档与工作流契约。
    limit: int = Field(ge=0)

    @model_validator(mode="before")
    @classmethod
    def upgrade_saved_query(cls, value: object) -> object:
        """旧步骤记录按 1 天 = 24 小时恢复；新写入只保留 hours。"""
        if isinstance(value, dict) and "days" in value:
            value = dict(value)
            days = value.pop("days")
            value.setdefault("hours", int(days) * 24)
        return value


class DiscoverOutput(FrozenModel):
    """从真实 ES 查询得到的原始日志数量与记录产物路径。"""

    # raw_count：字段说明见该模型文档与工作流契约。
    raw_count: int = 0
    # es_hits：字段说明见该模型文档与工作流契约。
    es_hits: int = 0
    # unparseable：字段说明见该模型文档与工作流契约。
    unparseable: int = 0


__all__ = ["DiscoverInput", "DiscoverOutput"]
