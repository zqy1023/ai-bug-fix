"""从真实日志流规范化错误文本，并直接交给去重；代表问题落 run 级产物。"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from typing import Any
from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt

from core.contracts import StepContext
from core.errors import StepFailed
from infrastructure.storage.run_control import cancellation_requested, pause_requested
from workflow.stages.log_pipeline.deduplicate_error.node import ignore_rules, write_groups
from workflow.stages.log_pipeline.discover_logs.models import DiscoverInput
from workflow.stages.log_pipeline.discover_logs.node import iter_raw
from workflow.stages.log_pipeline.discover_logs.normalizer import normalize_error
from .models import (
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
    progress: Callable[[dict[str, int]], None] | None = None,
    scan_state: dict[str, Any] | None = None,
    start_index: int = 0,
) -> Iterator[dict[str, Any]]:
    """按发现步骤记录的查询条件逐条拉取并规范化，不把全量记录收进列表。"""
    if data.run_id != ctx.run.run_id:
        raise StepFailed("NormalizeErrorInput.run_id 与执行上下文不一致")
    def control() -> bool:
        if cancellation_requested(ctx.store.root, ctx.run.run_id):
            return True
        if pause_requested(ctx.store.root, ctx.run.run_id):
            interrupt({"control": "pause", "run_id": ctx.run.run_id, "step": "normalize_error"})
        return False

    for index, record in enumerate(iter_raw(
        ctx, query, stats, progress=progress,
        cancel_requested=control, scan_state=scan_state,
    ), start=start_index):
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
    # 上游日志常把 response / track_config 嵌在 req 对象里，而不是放在记录顶层。
    # 顶层优先，缺失时回退到 req 内部；两者都统一成 JSON 字符串，供重放使用。
    response = record.get("response") or request.get("response") or ""
    from infrastructure.es.request_context import extract_track_config

    track_config = extract_track_config(record)
    if not isinstance(response, str):
        response = json.dumps(response, ensure_ascii=False)
    if not isinstance(track_config, str):
        track_config = json.dumps(track_config, ensure_ascii=False)
    return NormalizedLog(
        source_index=index,
        service_name=service_name,
        biztype=biztype,
        carrier=carrier,
        req=req,
        response=response,
        track_config=track_config,
        raw_error=raw_error,
        message=str(record.get("message") or ""),
        normalized_error=normalized,
    ).model_dump()


def run(ctx: StepContext, data: NormalizeErrorInput) -> NormalizeErrorOutput:
    """逐条规范化后直接去重。代表问题落 run 级产物，计数落在步骤记录里。"""
    funnel: dict[str, int] = {}
    query = _query(ctx)
    checkpoint_path = "artifacts/log_scan_checkpoint.json"
    path = ctx.store.run_dir(ctx.run.run_id) / checkpoint_path
    saved = ctx.store.read_artifact(ctx.run.run_id, checkpoint_path) if path.is_file() else {}
    if saved and saved.get("query") != query.model_dump():
        raise StepFailed("暂停前后的 ES 查询参数不一致，拒绝混合两次扫描结果")
    scan_state = saved.get("scan") or {}
    group_state = saved.get("grouping") or {}
    last_report = 0.0
    observed: dict[str, int] = {}

    def report(counts: dict[str, int]) -> None:
        nonlocal last_report
        observed.update(counts)
        now = time.monotonic()
        if now - last_report < 1:
            return
        last_report = now
        ctx.store.report_progress(ctx.run.run_id, "normalize_error", "正在读取并规范化 ES 日志", dict(observed))

    try:
        groups = write_groups(
            iter_normalized(ctx, data, query, funnel, report, scan_state,
                            start_index=int(group_state.get("input_count") or 0)),
            ignore_rules(ctx), report, state=group_state,
        )
    except GraphInterrupt:
        ctx.store.write_artifact(ctx.run.run_id, checkpoint_path, {
            "query": query.model_dump(), "scan": scan_state, "grouping": group_state,
        })
        ctx.store.report_progress(ctx.run.run_id, "normalize_error", "日志扫描已暂停，已保存本轮扫描进度", dict(observed))
        raise
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
    ctx.emit_artifact("log_parse_failures", "artifacts/log_parse_failures.json",
                      {"unparseable": funnel.get("unparseable", 0),
                       **(scan_state.get("parse_failures") or {"counts": {}, "samples": []})})
    path.unlink(missing_ok=True)
    observed.update(funnel)
    observed.update({**counts, "normalized_count": groups["input_count"]})
    ctx.store.report_progress(
        ctx.run.run_id, "normalize_error",
        "已达到扫描上限，当前结果为部分日志" if funnel.get("limit_reached") else "日志读取与规范化已完成",
        observed,
    )
    return NormalizeErrorOutput(
        run_id=data.run_id,
        es_hits=funnel.get("es_hits", 0),
        unparseable=funnel.get("unparseable", 0),
        record_limit=funnel.get("record_limit", query.limit),
        truncated=bool(funnel.get("limit_reached")),
        **counts,
    )


__all__ = ["iter_normalized", "run"]

