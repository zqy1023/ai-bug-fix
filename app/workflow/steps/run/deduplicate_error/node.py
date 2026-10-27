"""按已配置忽略规则和稳定业务键去重。代表问题由 normalize_error 落 run 级产物。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterator
from typing import Any

from app.application.context import StepContext
from app.domain.errors import StepFailed
from app.workflow.steps.run.deduplicate_error.models import (
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


# 去重键忽略时间值本身，否则同一异常在不同时间发生会被拆成多个任务；
# 展示给 Agent 的 normalized_error 仍保留真实时间戳，供判断解析格式。
_VOLATILE_TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)


def _key(record: dict[str, Any]) -> str:
    identity = [
        record["service_name"],
        record["biztype"],
        record["carrier"],
        _VOLATILE_TIMESTAMP.sub("<timestamp>", record["normalized_error"]),
    ]
    encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def consume(
    grouped: dict[str, dict[str, Any]],
    record: dict[str, Any],
    ignore_errors: tuple[str, ...],
) -> str:
    """并入一个分组。返回 ignored、new 或 duplicate。"""
    error = record["normalized_error"]
    if any(rule in error for rule in ignore_errors):
        return "ignored"
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
        # 保留可选 response/track_config，供 verify_issue 执行真实重放。
        for optional in ("response", "track_config", "message"):
            if optional in record and record.get(optional):
                grouped[key]["problem"][optional] = record[optional]
        return "new"
    entry["occurrence_count"] += 1
    return "duplicate"


def write_groups(
    records: Iterator[dict[str, Any]], ignore_errors: tuple[str, ...],
    progress: Callable[[dict[str, int]], None] | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """消费规范化记录并返回代表问题。去重结果的落盘由 normalize_error 负责。"""
    checkpoint = state if state is not None else {}
    grouped: dict[str, dict[str, Any]] = checkpoint.setdefault("groups", {})
    input_count = int(checkpoint.get("input_count") or 0)
    ignored_count = int(checkpoint.get("ignored_count") or 0)
    for position, value in enumerate(records):
        input_count += 1
        outcome = consume(grouped, _record(value, position), ignore_errors)
        if outcome == "ignored":
            ignored_count += 1
        checkpoint.update(input_count=input_count, ignored_count=ignored_count)
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

