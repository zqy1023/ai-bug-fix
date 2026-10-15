"""ES 异常日志读取、解析和分类保存适配器。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any


class ESLogAdapter:
    """按显式查询条件读取 ES，并规范化单条日志。"""

    def __init__(
        self,
        client: Any,
        scanner: Any,
        index_provider: Any,
        tz_offset: int,
    ) -> None:
        """注入 ES 客户端、扫描器、索引提供器与业务时区。"""
        self._client = client
        self._scanner = scanner
        self._index_provider = index_provider
        self._tz_offset = int(tz_offset)

    def fetch(
        self,
        *,
        service_name: str,
        service_prefix: str,
        excluded_messages: list[str],
        excluded_biztypes: list[str],
        days: int,
        limit: int,
        stats: dict[str, int] | None = None,
    ) -> Iterator[dict[str, str]]:
        """逐条拉取异常并解析为记录，不写文件，也不把全量命中收进内存。

        ``stats`` 传入时可拿到拉取漏斗：``es_hits``（ES 命中）、``unparseable``（message
        缺少可解析的 ``req:`` 结构）、``parsed``（进入后续步骤的记录数）。错误过滤与去重
        由独立的 ``deduplicate_error`` 步骤完成。
        """
        funnel = {"es_hits": 0, "unparseable": 0, "parsed": 0}
        # 索引名按 UTC 日期切，而拉取窗口按业务时区：本地一整天一定横跨两个
        # UTC 日期索引（0 点后的日志在"昨天"的索引里），所以多取一个索引。
        indices = self._index_provider("logstash-gather-", days + 1)
        gte = "now/d" if days == 1 else f"now-{days - 1}d/d"
        # ES 的 now/d 默认按 UTC 切零点；必须显式给业务时区，否则会漏掉本地 0 点后的日志
        sign = "+" if self._tz_offset >= 0 else "-"
        minutes = abs(self._tz_offset) * 60
        tz_str = f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"
        query = {
            "query": {
                "bool": {
                    "filter": [
                        {"prefix": {"service.name": service_prefix}},
                        {"match_phrase": {"service_name": service_name}},
                        {
                            "range": {
                                "@timestamp": {"gte": gte, "lt": "now+1d/d", "time_zone": tz_str}
                            }
                        },
                    ],
                    "must_not": [
                        {"match_phrase": {"message": rule}} for rule in excluded_messages
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
            size=100,
            scroll="2m",
            raise_on_error=True,
        )
        for hit in hits:
            funnel["es_hits"] += 1
            row = self.parse_hit(hit)
            if row is None:
                funnel["unparseable"] += 1
                continue
            funnel["parsed"] += 1
            yield row
            if limit and funnel["parsed"] >= limit:
                break
        if stats is not None:
            stats.update(funnel)

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
            # 没有 " error:" 标记，尝试从 message 前缀提取错误主题
            # 格式如：2026-10-15 08:07:27 [0] ERROR gather.py.track - 时间格式错误:...
            prefix = message[:req_index].strip()
            # 提取最后一个 " - " 后面的部分作为错误描述
            if " - " in prefix:
                error = prefix.split(" - ")[-1].strip()
            else:
                # 无法提取错误信息，放弃此条日志
                return None

        if not error:
            return None

        result = {
            "carrier": carrier,
            "req": json.dumps(req_obj, ensure_ascii=False, separators=(",", ":")),
            "error": error,
        }
        if track_config:
            result["track_config"] = track_config
        if response:
            result["response"] = response
        return result

