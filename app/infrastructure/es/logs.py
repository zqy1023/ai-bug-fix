"""ES 异常日志读取、解析和分类保存适配器。"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any

from config.settings import settings
from app.domain.errors import StepFailed


class ESLogAdapter:
    """按显式查询条件读取 ES，并规范化单条日志。"""

    def __init__(
        self,
        client: Any,
        scanner: Any,
        index_provider: Any,
        tz_offset: int,
        *,
        index_prefix: str = settings.es_index_prefix,
        scan_size: int = settings.es_scan_size,
        scroll: str = settings.es_scroll,
        excluded_messages: list[str] | None = None,
        request_timeout: int = settings.es_timeout_seconds,
    ) -> None:
        """注入 ES 客户端、扫描器、索引提供器与业务时区。"""
        self._client = client
        self._scanner = scanner
        self._index_provider = index_provider
        self._tz_offset = int(tz_offset)
        self._index_prefix = index_prefix
        self._scan_size = max(1, int(scan_size))
        self._scroll = scroll
        self._request_timeout = max(1, int(request_timeout))
        self._excluded_messages = list(
            settings.default_excluded_messages if excluded_messages is None else excluded_messages
        )

    def fetch(
        self,
        *,
        service_name: str,
        service_prefix: str,
        excluded_messages: list[str],
        excluded_biztypes: list[str],
        hours: int,
        limit: int,
        stats: dict[str, int] | None = None,
        progress: Callable[[dict[str, int]], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        scan_state: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, str]]:
        """逐条拉取异常并解析为记录，不写文件，也不把全量命中收进内存。

        ``stats`` 传入时可拿到拉取漏斗：``es_hits``（ES 命中）、``unparseable``（message
        缺少可解析的 ``req:`` 结构）、``parsed``（进入后续步骤的记录数）。错误过滤与去重
        由独立的 ``deduplicate_error`` 步骤完成。
        """
        if hours < 1 or limit < 0:
            raise ValueError("日志小时数必须大于零，条数上限不能为负数")
        funnel = {"es_hits": 0, "unparseable": 0, "parsed": 0,
                  "record_limit": limit, "limit_reached": 0}
        checkpoint = scan_state if scan_state is not None else {}
        funnel.update(checkpoint.get("stats") or {})
        checkpoint["stats"] = funnel
        seen = checkpoint.setdefault("seen_ids", {}) if scan_state is not None else None
        started = time.monotonic()
        last_progress = started
        last_control = started - 1

        def check_control() -> None:
            nonlocal last_control
            now = time.monotonic()
            should_check = now - last_control >= 0.5
            if should_check:
                last_control = now
            if should_check and cancel_requested and cancel_requested():
                raise StepFailed("日志扫描收到停止请求", result="manual", output={"control_cancelled": True})

        def publish() -> None:
            if stats is not None:
                stats.update(funnel)
            if progress:
                progress(dict(funnel))

        check_control()
        publish()
        if limit and funnel["parsed"] >= limit:
            return
        # 按精确小时窗口查询，跨日只扩展索引覆盖，不把查询窗口取整到零点。
        indices = self._index_provider(self._index_prefix, ceil(hours / 24) + 1)
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=hours)
        checkpoint.setdefault("start", start.isoformat())
        checkpoint.setdefault("end", end.isoformat())
        query = {
            "_source": ["service_name", "biztype", "message"],
            "query": {
                "bool": {
                    "filter": [
                        {"prefix": {"service.name": service_prefix}},
                        {"match_phrase": {"service_name": service_name}},
                        {
                            "range": {
                                "@timestamp": {"gte": checkpoint["start"], "lt": checkpoint["end"]}
                            }
                        },
                    ],
                    "must_not": [
                        {"match_phrase": {"message": rule}}
                        for rule in dict.fromkeys([*excluded_messages, *self._excluded_messages])
                    ] + [
                        {"match_phrase": {"biztype": biztype}} for biztype in excluded_biztypes
                    ],
                }
            },
        }
        hits = self._scanner(
            client=self._client,
            query=query,
            index=",".join(indices),
            size=self._scan_size,
            scroll=self._scroll,
            raise_on_error=True,
            request_timeout=self._request_timeout,
            scroll_kwargs={"request_timeout": self._request_timeout},
        )
        try:
            for hit in hits:
                check_control()
                if seen is not None and hit.get("_id") is not None:
                    identity = f"{hit.get('_index', '')}/{hit['_id']}"
                    if identity in seen:
                        continue
                    seen[identity] = True
                funnel["es_hits"] += 1
                row = self.parse_hit(hit)
                if row is None:
                    funnel["unparseable"] += 1
                    source = hit.get("_source") or {}
                    message = str(source.get("message") or "")
                    missing = [key for key in ("service_name", "biztype", "message")
                               if not isinstance(source.get(key), str) or not source[key]]
                    reason = "missing_required_fields" if missing else "missing_req_marker" if "req:" not in message else "invalid_request_or_error_structure"
                    failures = checkpoint.setdefault("parse_failures", {"counts": {}, "samples": []})
                    failures["counts"][reason] = failures["counts"].get(reason, 0) + 1
                    if len(failures["samples"]) < 20:
                        from app.infrastructure.redaction import redact_output
                        failures["samples"].append({"reason": reason, "missing_fields": missing,
                                                   "message_preview": redact_output(message)[:1000]})
                else:
                    funnel["parsed"] += 1
                    yield row
                now = time.monotonic()
                if now - last_progress >= 1:
                    publish()
                    last_progress = now
                if limit and funnel["parsed"] >= limit:
                    funnel["limit_reached"] = 1
                    break
            check_control()
        finally:
            try:
                close = getattr(hits, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        logging.getLogger(__name__).warning("清理 ES scroll 失败", exc_info=True)
            finally:
                publish()

    @classmethod
    def parse_hit(cls, hit: dict[str, Any]) -> dict[str, str] | None:
        """解析单条 ES hit，要求 ``service_name`` 和 ``biztype`` 为顶层字段。"""
        source = hit.get("_source", {})
        service_name = source.get("service_name")
        biztype = source.get("biztype")
        message = source.get("message")
        if not all(isinstance(value, str) and value for value in (service_name, biztype, message)):
            return None
        parsed = cls.parse_message(message)
        if parsed is None:
            return None
        return {
            "service_name": service_name,
            "biztype": biztype,
            **parsed,
        }

    @staticmethod
    def parse_message(message: str) -> dict[str, str] | None:
        """从异常 message 解析请求、响应、配置和错误文本。

        支持格式：
        1. 标准格式：... req:{"carrier":"XXX", ...} error: 错误信息
        2. 扩展格式：... carrier:XXX, req:{...} (错误信息在前面)
        3. 带响应格式：... req:{...} response:{...} error: 错误信息
        4. 完整格式：... req:{...} track_config:{...} response:{...} error: 错误信息

        response 和 track_config 为可选字段，用于 response 重放验证。
        """
        import re

        req_marker = "req:"
        error_marker = " error:"
        req_index = message.find(req_marker)
        if req_index < 0:
            return None

        # 解析 req JSON 对象
        req_start = req_index + len(req_marker)
        try:
            req_obj, req_length = json.JSONDecoder().raw_decode(message[req_start:].lstrip())
        except json.JSONDecodeError:
            return None
        if not isinstance(req_obj, dict):
            return None

        # 提取 carrier：优先从 req_obj 内部取，若无则从 message 中正则提取
        carrier = req_obj.get("carrier")
        if not isinstance(carrier, str) or not carrier.strip():
            # 尝试从 req: 前面提取 carrier:XXX 格式
            prefix = message[:req_index]
            carrier_match = re.search(r'carrier\s*:\s*([A-Za-z0-9_-]+)', prefix, re.IGNORECASE)
            if carrier_match:
                carrier = carrier_match.group(1)
            else:
                # 没有找到有效 carrier，放弃此条日志
                return None

        # 尝试提取 track_config (可选字段)
        track_config_marker = " track_config:"
        track_config = ""
        search_start = req_start + req_length
        track_config_index = message.find(track_config_marker, search_start)
        if track_config_index >= 0:
            track_config_start = track_config_index + len(track_config_marker)
            try:
                track_config_obj, track_config_length = json.JSONDecoder().raw_decode(
                    message[track_config_start:].lstrip()
                )
                if isinstance(track_config_obj, dict):
                    track_config = json.dumps(track_config_obj, ensure_ascii=False, separators=(",", ":"))
                    search_start = track_config_start + track_config_length
            except json.JSONDecodeError:
                pass  # track_config 解析失败，继续处理

        # 尝试提取 response (可选字段)
        response_marker = " response:"
        response = ""
        response_index = message.find(response_marker, search_start)
        if response_index >= 0:
            response_start = response_index + len(response_marker)
            try:
                response_obj, response_length = json.JSONDecoder().raw_decode(message[response_start:].lstrip())
                if isinstance(response_obj, (dict, list, str)):
                    response = json.dumps(response_obj, ensure_ascii=False, separators=(",", ":"))
                    search_start = response_start + response_length
            except json.JSONDecodeError:
                pass  # response 解析失败，继续处理 error

        # 提取错误信息：优先找 " error:" 后面的文本，若无则提取日志前缀的错误主题
        error_index = message.find(error_marker, search_start)
        if error_index >= 0:
            error = message[error_index + len(error_marker):].strip()
        else:
            # 没有显式 error: 字段时，保留完整原始 message。
            # 日志主题后可能包含 model/key/req 等字段；按逗号或 req: 截断会丢失关键信息。
            error = message.strip()

        if not error:
            return None

        result = {
            "carrier": carrier,
            "req": json.dumps(req_obj, ensure_ascii=False, separators=(",", ":")),
            "error": error,
        }
        if track_config:
            result["track_config"] = track_config
        # 显式 error: 后可能只剩异常摘要，必须在丢弃 message 上下文前保留代理渠道。
        from app.workflow.steps.verify_issue.replay_model import extract_track_config

        recovered_config = extract_track_config({"req": req_obj, "track_config": track_config, "message": message})
        if recovered_config:
            result["track_config"] = json.dumps(recovered_config, ensure_ascii=False, separators=(",", ":"))
        if response:
            result["response"] = response
        return result

