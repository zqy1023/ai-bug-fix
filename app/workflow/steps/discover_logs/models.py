"""01 拉取日志的输入输出契约。"""

from __future__ import annotations

from app.workflow.spec import FrozenModel
from pydantic import Field


class DiscoverInput(FrozenModel):
    """实际 ES 查询口径；不支持注入样本日志。"""

    service_name: str = Field(min_length=1)
    service_prefix: str = Field(min_length=1)
    excluded_messages: list[str] = Field(default_factory=list)
    excluded_biztypes: list[str] = Field(default_factory=list)
    days: int = Field(ge=1)
    limit: int = Field(ge=0)


class DiscoverOutput(FrozenModel):
    """从真实 ES 查询得到的原始日志数量与记录产物路径。"""

    raw_count: int = 0
    es_hits: int = 0
    unparseable: int = 0


__all__ = ["DiscoverInput", "DiscoverOutput"]
