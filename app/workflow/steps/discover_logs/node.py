"""从配置的 ES 查询原始日志，并将结果作为后续步骤的唯一输入 artifact。"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from app.domain.errors import StepFailed
from app.application.context import StepContext
from app.workflow.steps.discover_logs.models import DiscoverInput, DiscoverOutput


def iter_raw(
    ctx: StepContext, data: DiscoverInput, stats: dict[str, int], *,
    progress: Callable[[dict[str, int]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> Iterator[dict[str, str]]:
    """按查询条件逐条读取 ES。调用方消费完迭代器后，``stats`` 才完整。"""
    if ctx.services.log_source is None:
        raise StepFailed("生产 ES 日志源未配置")
    raw = ctx.services.log_source.fetch(
        service_name=data.service_name,
        service_prefix=data.service_prefix,
        excluded_messages=list(data.excluded_messages),
        excluded_biztypes=list(data.excluded_biztypes),
        hours=data.hours,
        limit=data.limit,
        stats=stats,
        progress=progress,
        cancel_requested=cancel_requested,
    )
    for record in raw:
        if not isinstance(record, dict):
            raise StepFailed("ES 日志记录必须是对象")
        yield record


def run(ctx: StepContext, data: DiscoverInput) -> DiscoverOutput:
    """查询条件随步骤记录写入 steps.json。全量记录由 normalize_error 消费，不在这里预扫。"""
    if ctx.services.log_source is None:
        raise StepFailed("生产 ES 日志源未配置")
    return DiscoverOutput()



