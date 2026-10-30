"""ES 异常日志读取、解析和分类保存适配器。"""

from __future__ import annotations

import ast
import json
import logging
import re
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any

from config.settings import settings
from core.errors import StepFailed


# 日志里偶见把占位值写进 carrier 字段。这类记录无法定位可写的 carrier 目录，
# 不能当成一个叫 "unknown" 的运输商建任务，必须在准入阶段就拒绝。
_PLACEHOLDER_CARRIERS = frozenset({"unknown", "unk", "none", "null", "n/a", "na", "-", "?"})


def _valid_carrier(value: Any) -> str:
    """返回可用运输商名；占位值/空值一律视为不可用。"""
    name = value.strip() if isinstance(value, str) else ""
    return "" if name.casefold() in _PLACEHOLDER_CARRIERS else name


# 内联标记：部分日志把 carrier/单号直接写在正文里，而不是包成 req JSON。
# 大小写混用真实存在（`carrier:` / `Carrier:`），单号字段也有 `num` / `number` 两种。
_INLINE_CARRIER = re.compile(r"carrier\s*[:=]\s*([A-Za-z0-9_.-]+)", re.IGNORECASE)
_INLINE_NUMBER = re.compile(r"\bnum(?:ber)?\s*[:=]\s*([A-Za-z0-9_-]+)", re.IGNORECASE)
_INLINE_BODY = re.compile(r"\b(?:body|msg)\s*[:=]\s*", re.IGNORECASE)


class ESLogAdapter:
    """按显式查询条件读取 ES，并规范化单条日志。"""

    def __init__(
        self,
        client: Any,
        scanner: Any,
        index_provider: Any,
        *,
        index_prefix: str = "logstash-gather-",
        scan_size: int = 1000,
        scroll: str = "2m",
        excluded_messages: list[str] | None = None,
        request_timeout: int = 30,
    ) -> None:
        """注入 ES 客户端、扫描器、索引提供器；时区由 index_provider 闭包负责。"""
        self._client = client
        self._scanner = scanner
        self._index_provider = index_provider
        self._index_prefix = index_prefix
        self._scan_size = max(1, int(scan_size))
        self._scroll = scroll
        self._request_timeout = max(1, int(request_timeout))
        self._excluded_messages = list(
            ("redis.exceptions.ConnectionError",) if excluded_messages is None else excluded_messages
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
            raise StepFailed(
                "日志小时数必须大于零，条数上限不能为负数", code="LOG_QUERY_ARGS_INVALID"
            )
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
                    if missing:
                        reason = "missing_required_fields"
                    elif any(marker in message for marker in ("req:", "model:", "requestResponse:")):
                        # 已知格式但内容结构不合法：与"格式本身不认识"分开计，便于定位。
                        reason = "invalid_request_or_error_structure"
                    elif "异常溯源:" in message:
                        # 溯源根不在 gathers/spiders 下，无法还原 carrier。
                        reason = "carrier_unresolved"
                    elif re.search(
                        r"carrier\s*[:=]\s*(?:unknown|unk|none|null|n/?a|-|[\"'])\s*[,}]",
                        message, re.IGNORECASE,
                    ):
                        # carrier 字段写的是占位值：不能当成运输商建任务。
                        reason = "carrier_unresolved"
                    else:
                        reason = "unrecognized_message_format"
                    failures = checkpoint.setdefault("parse_failures", {"counts": {}, "samples": []})
                    failures["counts"][reason] = failures["counts"].get(reason, 0) + 1
                    if len(failures["samples"]) < 20:
                        failures["samples"].append({"reason": reason, "missing_fields": missing,
                                                   "message_preview": message[:1000]})
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
            "message": message,
        }

    @staticmethod
    def parse_message(message: str) -> dict[str, str] | None:
        """从异常 message 解析请求、响应、配置和错误文本。

        支持格式：
        1. 标准格式：... req:{"carrier":"XXX", ...} error: 错误信息
        2. 扩展格式：... carrier:XXX, req:{...} (错误信息在前面)
        3. 带响应格式：... req:{...} response:{...} error: 错误信息
        4. 完整格式：... req:{...} track_config:{...} response:{...} error: 错误信息
        5. 解析异常格式：... framework.track_server:解析异常,model:{"carrier":"XXX",
           "requests":[...],"track_config":{...},"response":"<原始响应正文>"}, msg:None
        6. 业务结果格式：requestResponse:{'code': 404, 'data': [{'number': '..',
           'carrier': '..'}], 'message': '..', 'error_type': 'ProcessError'}
        7. 异常溯源格式：... <module>:<error>, 异常溯源:gathers.<Carrier>.main_batch
           （无 req/response，但溯源路径能确定性还原 carrier）
        8. 内联格式：... <module>:<error>, carrier:<X>, num:<Y>（carrier 与单号写在正文里）

        ``response`` 和 ``track_config`` 为可选字段，用于 response 重放验证。
        格式 5 的 ``response`` 是原始响应正文，也是"official 坏数据还是本地解析缺陷"
        的唯一判据；它过去因为 message 里没有 ``req:`` 标记而被整条丢弃，导致归一化
        产物中的 ``response`` 永远为空。
        """
        # 按标记依次尝试：响应正文里也可能出现 "req:" 等字样，单一判据会误选解析器。
        parsers = (
            ("req:", ESLogAdapter._parse_request_marker),
            ("model:", ESLogAdapter._parse_model_marker),
            ("requestResponse:", ESLogAdapter._parse_request_response),
            ("异常溯源:", ESLogAdapter._parse_trace_marker),
        )
        for marker, parser in parsers:
            if marker not in message:
                continue
            parsed = parser(message)
            if parsed is not None:
                return parsed
        # 兜底：`<module>:<error>, carrier:<X>, num:<Y>` 这类没有标记的内联格式。
        return ESLogAdapter._parse_inline_marker(message)

    @staticmethod
    def _parse_request_marker(message: str) -> dict[str, str] | None:
        """格式 1-4：以 ``req:`` 标记带出请求、响应和配置。"""
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
        carrier = _valid_carrier(carrier)
        if not carrier:
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
            "message_format": "req",
        }
        if track_config:
            result["track_config"] = track_config
        # 显式 error: 后可能只剩异常摘要，必须在丢弃 message 上下文前保留代理渠道。
        from infrastructure.es.request_context import extract_track_config

        recovered_config = extract_track_config({"req": req_obj, "track_config": track_config, "message": message})
        if recovered_config:
            result["track_config"] = json.dumps(recovered_config, ensure_ascii=False, separators=(",", ":"))
        if response:
            result["response"] = response
        return result

    @staticmethod
    def _parse_model_marker(message: str) -> dict[str, str] | None:
        """格式 5：``model:{...}`` 解析异常，携带 carrier/requests/track_config/response。"""
        marker = "model:"
        index = message.find(marker)
        if index < 0:
            return None
        payload_text = message[index + len(marker):].lstrip()
        try:
            payload, length = json.JSONDecoder().raw_decode(payload_text)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        carrier = _valid_carrier(payload.get("carrier"))
        if not carrier:
            return None
        requests = payload.get("requests")
        if not isinstance(requests, list):
            requests = []
        request: dict[str, Any] = {"carrier": carrier, "requests": requests}
        track_config = payload.get("track_config")
        if isinstance(track_config, dict) and track_config:
            request["track_config"] = track_config
        error = ESLogAdapter._log_topic(message, index)
        if not error:
            # 缺少 logger 前缀时仍保留记录：carrier/requests/response 组合仍有价值。
            error = "解析异常"
        # 只在 JSON 正文之后找 msg:，避免响应正文里出现 "msg:" 时误取尾部文本。
        tail = payload_text[length:]
        msg_index = tail.find("msg:")
        if msg_index >= 0:
            detail = tail[msg_index + len("msg:"):].strip()
            if detail and detail.casefold() not in {"none", "null"}:
                error = f"{error} | msg: {detail}"
        result = {
            "carrier": carrier,
            "req": json.dumps(request, ensure_ascii=False, separators=(",", ":")),
            "error": error,
            "message_format": "model",
        }
        if isinstance(track_config, dict) and track_config:
            result["track_config"] = json.dumps(
                track_config, ensure_ascii=False, separators=(",", ":")
            )
        response = ESLogAdapter._json_text(payload.get("response"))
        if response:
            result["response"] = response
        return result

    @staticmethod
    def _parse_request_response(message: str) -> dict[str, str] | None:
        """格式 6：``requestResponse:{...}`` 业务结果（Python repr，非 JSON）。

        这类记录是下游症状（例如日期串带 ``Z`` 触发 ``ProcessError``），不是"无单"的同义词，
        因此必须保留 ``message``/``error_type`` 原文，交给归因层判断官方还是本地。
        """
        marker = "requestResponse:"
        index = message.find(marker)
        if index < 0:
            return None
        payload = ESLogAdapter._literal_object(message[index + len(marker):])
        if payload is None:
            return None
        carrier = payload.get("carrier") if isinstance(payload.get("carrier"), str) else None
        requests: list[dict[str, str]] = []
        data = payload.get("data")
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                number = item.get("number")
                if not isinstance(number, str) or not number:
                    continue
                requests.append({
                    "number": number,
                    "gather_channel": str(item.get("gather_channel") or ""),
                })
                if not carrier and isinstance(item.get("carrier"), str) and item["carrier"]:
                    carrier = item["carrier"]
        if not carrier:
            return None
        carrier = _valid_carrier(carrier)
        if not carrier:
            return None
        error = "requestResponse: code={code}, error_type={kind}, message={msg}".format(
            code=payload.get("code"),
            kind=payload.get("error_type") or "",
            msg=payload.get("message") or "",
        ).strip()
        return {
            "carrier": carrier,
            "req": json.dumps(
                {"carrier": carrier, "requests": requests},
                ensure_ascii=False, separators=(",", ":"),
            ),
            "error": error,
            "message_format": "requestResponse",
        }

    @staticmethod
    def _log_topic(message: str, end_index: int) -> str:
        """取日志主题：最后一个 `` - `` 之后、json 标记之前的文本。"""
        prefix = message[:end_index]
        cut = prefix.rfind(" - ")
        topic = prefix[cut + 3:] if cut >= 0 else prefix
        return topic.strip().rstrip(",").strip()

    @staticmethod
    def _json_text(value: Any) -> str:
        """把响应正文统一成字符串；对象走紧凑 JSON，避免二次转义丢信息。"""
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return ""

    @staticmethod
    def _literal_object(text: str) -> dict[str, Any] | None:
        """解析 Python repr 字典；日志尾部可能还有文本，按括号配对截取。"""
        start = text.find("{")
        if start < 0:
            return None
        depth = 0
        in_string = False
        escaped = False
        quote = ""
        for pos in range(start, len(text)):
            char = text[pos]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    in_string = False
                continue
            if char in "'\"":
                in_string = True
                quote = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = ast.literal_eval(text[start:pos + 1])
                    except (ValueError, SyntaxError):
                        return None
                    return value if isinstance(value, dict) else None
        return None

    @staticmethod
    def _parse_trace_marker(message: str) -> dict[str, str] | None:
        """格式 7：``<module>:<error>, 异常溯源:<模块路径>``。

        这类记录没有 req、没有 response（典型是共享层抛出的"未知时区"），
        但 ``异常溯源:gathers.<Carrier>.main_batch`` 能确定性还原 carrier，
        过去因为缺 ``req:`` 被整条丢弃。没有订单号不等于没有归属。
        """
        marker = "异常溯源:"
        index = message.find(marker)
        if index < 0:
            return None
        topic = message[:index].rstrip()
        cut = topic.rfind(" - ")
        if cut >= 0:
            topic = topic[cut + 3:]
        topic = topic.strip().rstrip(",").strip()
        if not topic:
            return None
        source = message[index + len(marker):].strip()
        parts = source.split(".")
        if len(parts) < 3 or parts[0] not in {"gathers", "spiders"}:
            return None
        carrier = _valid_carrier(parts[1])
        if not carrier:
            return None
        return {
            "carrier": carrier,
            "req": json.dumps(
                {"carrier": carrier, "source": source},
                ensure_ascii=False, separators=(",", ":"),
            ),
            "error": topic,
            "message_format": "trace",
        }

    @staticmethod
    def _parse_inline_marker(message: str) -> dict[str, str] | None:
        """格式 8：``<module>:<error>, carrier:<X>, num:<Y>``（内联 carrier，无 req JSON）。

        典型是旧框架桥接层的「时间解析错误」。这类记录带着明确的 carrier 与单号，
        可以直接定位到 carrier；没有它就只能整条丢弃。
        """
        if any(marker in message for marker in ("req:", "model:", "requestResponse:")):
            return None
        match = _INLINE_CARRIER.search(message)
        if not match:
            return None
        carrier = _valid_carrier(match.group(1))
        if not carrier:
            return None
        topic = message[:match.start()].rstrip().rstrip(",").strip()
        cut = topic.rfind(" - ")
        if cut >= 0:
            topic = topic[cut + 3:]
        topic = topic.strip()
        if not topic:
            return None
        request: dict[str, Any] = {"carrier": carrier}
        number = _INLINE_NUMBER.search(message, match.end())
        if number:
            request["requests"] = [{"number": number.group(1)}]
        result = {
            "carrier": carrier,
            "req": json.dumps(request, ensure_ascii=False, separators=(",", ":")),
            "error": topic,
            "message_format": "inline",
        }
        # body= 之后是原始响应正文（常见于拦截/验证页），保留下来供归因重放。
        body = _INLINE_BODY.search(message, match.end())
        if body:
            text = message[body.end():].strip()
            if text:
                result["response"] = text
        return result

