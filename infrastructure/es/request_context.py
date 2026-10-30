"""从原始日志还原请求配置，供分析 Agent 使用。"""

from __future__ import annotations

import json
import re
from typing import Any


def extract_track_config(record: dict[str, Any]) -> dict[str, Any]:
    """还原请求配置，优先使用结构字段，缺少渠道时从原异常提取。

    Args:
        record: 原异常记录，允许 req/track_config 使用 JSON 字符串。

    Returns:
        原始请求配置；保留原超时，不猜测渠道值。
    """
    def object_value(value: Any) -> dict[str, Any]:
        if isinstance(value, str) and value.strip():
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return {}
        return dict(value) if isinstance(value, dict) else {}

    request = object_value(record.get("req"))
    config = {**object_value(request.get("track_config")), **object_value(record.get("track_config"))}
    channel = config.get("proxy_channel_model") or record.get("proxy_channel_model")
    if not channel:
        for key in ("raw_error", "error", "message"):
            text = record.get(key)
            if not isinstance(text, str):
                continue
            for match in re.finditer(r'"proxy_channel_model"\s*:\s*', text):
                try:
                    candidate, _ = json.JSONDecoder().raw_decode(text[match.end():])
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict) and candidate:
                    channel = candidate
                    break
            if channel:
                break
    if channel:
        config["proxy_channel_model"] = channel
        # 业务模型优先读取 compact_proxy；残留的旧代理串会使恢复的渠道完全不生效。
        config.pop("proxy", None)
        config.pop("compact_proxy", None)
    return config
