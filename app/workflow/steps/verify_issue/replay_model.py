"""重放用的请求还原。

req 在 ES 日志里有两种形状：完整的 TrackModel dump（含 requests/track_config/response）
和单个请求项。两种都必须能还原成带单号的请求，否则重放跑的是空单号。
"""

from __future__ import annotations

import json
import re
from typing import Any


def extract_track_config(record: dict[str, Any]) -> dict[str, Any]:
    """还原请求配置，优先使用结构字段，缺少渠道时从原异常提取。

    Args:
        record: 原异常记录或重放输入，允许 req/track_config 使用 JSON 字符串。

    Returns:
        可传给 TrackRequestConfigModel 的配置；保留原超时，不猜测渠道值。
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


def request_numbers(req_data: dict[str, Any]) -> list[str]:
    """取出 req 里的单号。req 有完整 TrackModel dump 和单条请求两种形状。"""
    items = req_data.get("requests")
    if not isinstance(items, list) or not items:
        items = [req_data]
    numbers: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        number = str(item.get("number") or "").strip()
        if number:
            numbers.append(number)
    return numbers


def prepare_request_runtime(model: Any, *, recorded: bool) -> None:
    """仅在线请求初始化代理转发配置，渠道不可用时禁止退化为直连。

    Args:
        model: 已还原代理渠道的 TrackModel。
        recorded: 固定正文模式，不初始化联网配置。
    """
    if recorded or not model.track_config.proxy_channel_model:
        return
    from yq_common.proxy_forward_utils.proxy_forward_config import proxy_forward_config

    proxy_forward_config.init()
    if not model.track_config.proxy:
        raise ValueError("代理渠道无法生成代理地址，停止请求回归；请检查代理转发配置")


def build_track_model(payload: dict[str, Any]) -> Any:
    """按记录里的 req 形状还原 TrackModel。

    业务模型定义在业务仓库，只有重放子进程里才 import 得到，所以放在函数内部。
    """
    from framework.models import (
        TrackModel,
        TrackRequestConfigModel,
        TrackRequestItemModel,
    )

    raw = payload.get("req")
    if not isinstance(raw, dict):
        raise ValueError("req 必须是 JSON 对象")
    items = raw.get("requests")
    if not isinstance(items, list) or not items:
        items = [raw]
    requests = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("req.requests 元素必须是 JSON 对象")
        requests.append(TrackRequestItemModel(**item))
    config = extract_track_config(payload)
    if isinstance(config.get("proxy_channel_model"), dict):
        from framework.models import ProxyChannelModel

        config["proxy_channel_model"] = ProxyChannelModel(**config["proxy_channel_model"])
    response = payload.get("response")
    if not isinstance(response, str) or not response:
        response = None
    return TrackModel(
        carrier=payload["carrier"],
        requests=requests,
        track_config=TrackRequestConfigModel(**config) if config else TrackRequestConfigModel(),
        response=response,
    )


__all__ = ["build_track_model", "extract_track_config", "prepare_request_runtime", "request_numbers"]
