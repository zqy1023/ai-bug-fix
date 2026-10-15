"""从真实日志流规范化错误文本，并直接交给去重；代表问题落 run 级产物。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from app.application.context import StepContext
from app.domain.errors import StepFailed
from app.workflow.steps.deduplicate_error.node import _ignore_rules, write_groups
from app.workflow.steps.discover_logs.models import DiscoverInput
from app.workflow.steps.discover_logs.node import iter_raw
from app.workflow.steps.discover_logs.normalizer import normalize_error
from app.workflow.steps.normalize_error.models import (
    NormalizedLog,
    NormalizeErrorInput,
    NormalizeErrorOutput,
)


def _query(ctx: StepContext) -> DiscoverInput:
    record = ctx.store.load(ctx.run.run_id, "discover_logs")
    if record is None or record.status != "succeeded":
        raise StepFailed("缺少已通过的 discover_logs；必须先完成真实 ES 日志读取")
    try:
        return DiscoverInput.model_validate(record.input)
    except ValueError as error:
        raise StepFailed("discover_logs 缺少可重放的查询条件") from error


def iter_normalized(
    ctx: StepContext,
    data: NormalizeErrorInput,
    query: DiscoverInput,
    stats: dict[str, int],
) -> Iterator[dict[str, Any]]:
    """按发现步骤记录的查询条件逐条拉取并规范化，不把全量记录收进列表。"""
    if data.run_id != ctx.run.run_id:
        raise StepFailed("NormalizeErrorInput.run_id 与执行上下文不一致")
    for index, record in enumerate(iter_raw(ctx, query, stats)):
        yield _normalize(record, index)


def _required_text(record: dict[str, Any], field: str, index: int) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise StepFailed(f"ES 第 {index} 条记录缺少有效 {field}")
    return value


def _normalize(record: Any, index: int) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise StepFailed(f"ES 第 {index} 条记录必须是对象")
    service_name = _required_text(record, "service_name", index)
    biztype = _required_text(record, "biztype", index)
    carrier = _required_text(record, "carrier", index)
    req = _required_text(record, "req", index)
    raw_error = _required_text(record, "error", index)
    try:
        request = json.loads(req)
    except json.JSONDecodeError as error:
        raise StepFailed(f"ES 第 {index} 条记录的 req 不是合法 JSON") from error
    if not isinstance(request, dict):
        raise StepFailed(f"ES 第 {index} 条记录的 req 必须是 JSON 对象")
    normalized = normalize_error(raw_error)
    if not normalized:
        raise StepFailed(f"ES 第 {index} 条记录的 error 规范化后为空")
    return NormalizedLog(
        source_index=index,
        service_name=service_name,
        biztype=biztype,
        carrier=carrier,
        req=req,
        response=record.get("response", ""),  # 可选字段
        track_config=record.get("track_config", ""),  # 可选字段
        raw_error=raw_error,
        normalized_error=normalized,
    ).model_dump()


def run(ctx: StepContext, data: NormalizeErrorInput) -> NormalizeErrorOutput:
    """逐条规范化后直接去重。代表问题落 run 级产物，计数落在步骤记录里。"""
    funnel: dict[str, int] = {}
    query = _query(ctx)
    groups = write_groups(iter_normalized(ctx, data, query, funnel), _ignore_rules(ctx))
    counts = {
        "record_count": groups["input_count"],
        "ignored_count": groups["ignored_count"],
        "duplicate_count": groups["duplicate_count"],
        "unique_count": len(groups["records"]),
    }
    # 先写产物再返回：引用必须指向一份已经完整落盘的去重结果。
    ctx.emit_artifact(
        "normalized_records",
        "artifacts/normalized_records.json",
        {"records": groups["records"], **counts},
    )
    return NormalizeErrorOutput(
        run_id=data.run_id,
        es_hits=funnel.get("es_hits", 0),
        unparseable=funnel.get("unparseable", 0),
        **counts,
    )


__all__ = ["iter_normalized", "run"]
