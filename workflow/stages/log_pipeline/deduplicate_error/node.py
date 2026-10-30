"""按已配置忽略规则和稳定业务键去重。代表问题由 normalize_error 落 run 级产物。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from typing import Any

from core.contracts import StepContext
from core.errors import StepFailed
from core.keys import stable_error
from workflow.stages.log_pipeline.classify import exception_tail
from .models import (
    DeduplicateErrorInput,
    DeduplicateErrorOutput,
    DeduplicatedLog,
)


def ignore_rules(ctx: StepContext) -> tuple[str, ...]:
    configured = ctx.services.ignore_errors
    if not isinstance(configured, list) or any(
        not isinstance(value, str) or not value.strip() for value in configured
    ):
        raise StepFailed("ignore_errors 必须是非空字符串组成的配置数组")
    return tuple(configured)


def _record(value: Any, position: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StepFailed(f"规范化记录第 {position} 条必须是对象")
    required_text = (
        "service_name",
        "biztype",
        "carrier",
        "req",
        "raw_error",
        "normalized_error",
    )
    for field in required_text:
        item = value.get(field)
        if not isinstance(item, str) or not item.strip():
            raise StepFailed(f"规范化记录第 {position} 条缺少有效 {field}")
    source_index = value.get("source_index")
    if not isinstance(source_index, int) or isinstance(source_index, bool) or source_index < 0:
        raise StepFailed(f"规范化记录第 {position} 条 source_index 无效")
    try:
        request = json.loads(value["req"])
    except json.JSONDecodeError as error:
        raise StepFailed(f"规范化记录第 {position} 条 req 不是合法 JSON") from error
    if not isinstance(request, dict):
        raise StepFailed(f"规范化记录第 {position} 条 req 必须是 JSON 对象")
    return value


# 去重键忽略时间值等易变字段，否则同一异常在不同时间发生会被拆成多个任务；
# 展示给 Agent 的 normalized_error 仍保留真实时间戳，供判断解析格式。
# 规则与 ``core.keys.problem_key`` 共用 ``stable_error``，避免两处各写一套。


def _key(record: dict[str, Any]) -> str:
    """去重键与 ``core.keys.problem_key`` 共用同一套易变字段清洗规则。"""
    identity = [
        record["service_name"],
        record["biztype"],
        record["carrier"],
        stable_error(record["normalized_error"]),
    ]
    encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# 分组里额外保留的字段：清洗分流、归因与审查都要用，且全部是字符串。
_OPTIONAL_FIELDS = (
    "response", "track_config", "message", "message_format",
    "exception_class", "exception_message", "frame", "field", "response_kind", "blame",
)


def consume(
    grouped: dict[str, dict[str, Any]],
    record: dict[str, Any],
    ignore_errors: tuple[str, ...],
) -> tuple[str, str]:
    """并入一个分组。返回 ``(结果, 原因)``；结果为 ignored/unrepairable/new/duplicate。"""
    error = record["normalized_error"]
    for rule in ignore_errors:
        if rule in error:
            return "ignored", f"ignore_rule:{rule}"
    # 传输层异常改业务代码修不了，不进修复池。仍计入 ignored 并保留原因，
    # 否则代理/SSL 长期故障会从报告里彻底消失，运维再也看不到。
    if str(record.get("blame") or "") == "transport":
        tail = exception_tail(str(record.get("exception_class") or ""))
        return "unrepairable", f"transport:{tail or 'unknown'}"
    key = _key(record)
    entry = grouped.get(key)
    if entry is None:
        grouped[key] = {
            "dedup_key": key,
            "service_name": record["service_name"],
            "biztype": record["biztype"],
            "carrier": record["carrier"],
            "error": error,
            "representative_index": record["source_index"],
            "occurrence_count": 1,
            "problem": {
                "service_name": record["service_name"],
                "biztype": record["biztype"],
                "carrier": record["carrier"],
                "req": record["req"],
                "error": error,
                "raw_error": record["raw_error"],
            },
        }
        # 保留可选响应与结构化分类，供清洗分流、归因与审查核对。
        for optional in _OPTIONAL_FIELDS:
            value = record.get(optional)
            if value:
                grouped[key]["problem"][optional] = str(value)
        return "new", ""
    entry["occurrence_count"] += 1
    return "duplicate", ""


def write_groups(
    records: Iterator[dict[str, Any]], ignore_errors: tuple[str, ...],
    progress: Callable[[dict[str, int]], None] | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """消费规范化记录，返回代表问题与清洗质量统计。"""
    checkpoint = state if state is not None else {}
    grouped: dict[str, dict[str, Any]] = checkpoint.setdefault("groups", {})
    reasons: dict[str, int] = checkpoint.setdefault("ignored_reasons", {})
    formats: dict[str, int] = checkpoint.setdefault("message_formats", {})
    classes: dict[str, int] = checkpoint.setdefault("exception_classes", {})
    input_count = int(checkpoint.get("input_count") or 0)
    ignored_count = int(checkpoint.get("ignored_count") or 0)
    response_present = int(checkpoint.get("response_present") or 0)
    for position, value in enumerate(records):
        input_count += 1
        record = _record(value, position)
        outcome, reason = consume(grouped, record, ignore_errors)
        if outcome in {"ignored", "unrepairable"}:
            ignored_count += 1
            if reason:
                reasons[reason] = reasons.get(reason, 0) + 1
        message_format = str(record.get("message_format") or "unknown")
        formats[message_format] = formats.get(message_format, 0) + 1
        exception_class = str(record.get("exception_class") or "")
        if exception_class:
            classes[exception_class] = classes.get(exception_class, 0) + 1
        if record.get("response"):
            response_present += 1
        checkpoint.update(
            input_count=input_count, ignored_count=ignored_count,
            response_present=response_present, message_formats=formats,
            exception_classes=classes, ignored_reasons=reasons,
        )
        if progress and input_count % 100 == 0:
            progress({"normalized_count": input_count, "ignored_count": ignored_count,
                      "unique_count": len(grouped), "duplicate_count": input_count - ignored_count - len(grouped)})
    output_records = [
        DeduplicatedLog.model_validate(value).model_dump()
        for value in grouped.values()
    ]
    return {
        "input_count": input_count,
        "ignored_count": ignored_count,
        "duplicate_count": input_count - ignored_count - len(output_records),
        "records": output_records,
        "quality": {
            "message_formats": dict(formats),
            "exception_classes": dict(classes),
            "ignored_reasons": dict(reasons),
            "response_present": response_present,
        },
    }


def run(ctx: StepContext, data: DeduplicateErrorInput) -> DeduplicateErrorOutput:
    """读取去重计数。分组已由 normalize_error 在同一次拉取中完成。"""
    if data.run_id != ctx.run.run_id:
        raise StepFailed("DeduplicateErrorInput.run_id 与执行上下文不一致")
    record = ctx.store.load(data.run_id, "normalize_error")
    if record is None or record.status != "succeeded":
        raise StepFailed("缺少已通过的 normalize_error")
    payload = record.output
    if not isinstance(payload, dict):
        raise StepFailed("normalize_error 记录必须包含计数")
    counts = {
        name: payload.get(name)
        for name in ("record_count", "ignored_count", "duplicate_count", "unique_count")
    }
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in counts.values()):
        raise StepFailed("normalize_error 记录缺少有效计数")
    return DeduplicateErrorOutput(
        run_id=data.run_id,
        input_count=counts["record_count"],
        ignored_count=counts["ignored_count"],
        unique_count=counts["unique_count"],
        duplicate_count=counts["duplicate_count"],
    )


__all__ = ["consume", "ignore_rules", "run", "write_groups"]

