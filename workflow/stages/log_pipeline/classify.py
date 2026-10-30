"""日志清洗用的确定性分类：异常类、响应形态、缺陷归属初判。

``blame`` 只做"定义上可判定"的归属判断，**不代替重放归因**，也不承诺补丁正确：

- ``transport``：网络/代理/SSL 传输层异常，改业务代码修不了；
- ``business``：业务结果（如 404 无单），不是代码缺陷；
- ``upstream``：上游返回的不是 JSON（拦截页/官方错误页），需重放确认；
- ``code``：异常栈落在本地代码里，是最需要修的形态；
- ``undetermined``：证据不足判不出来。注意这是"判不出归属"，**不是"未知运输商"**；
  carrier 是独立字段，必须来自日志原值。

命名刻意避开 ``unknown``：本项目里 ``category="unknown"``（根因类别）和
``code_verdict="UNKNOWN"``（代码结论）已经占用该词，再用一次会混淆。
"""

from __future__ import annotations

import json
import re

# 定义上不属于业务逻辑的错误：发生在请求/传输层，改 carrier 解析代码无法修复。
# 名单只收"传输层"类，宁可少收也不误伤业务异常（误杀会漏掉真缺陷）。
TRANSPORT_EXCEPTION_CLASSES = frozenset({
    "ProxyError",
    "SSLError",
    "SSLEOFError",
    "ConnectionError",
    "ConnectTimeout",
    "ConnectTimeoutError",
    "ReadTimeout",
    "ReadTimeoutError",
    "TimeoutError",
    "ChunkedEncodingError",
    "NewConnectionError",
    "MaxRetryError",
    "ProtocolError",
    "LocalProtocolError",
    "RemoteDisconnected",
    "IncompleteRead",
})

# 业务结果里表示"请求本身有问题/无单"的码，不是代码缺陷。
_BUSINESS_CODES = frozenset({"400", "401", "403", "404"})
_CODE = re.compile(r"code=(\d+)")
_ERROR_TYPE = re.compile(r"error_type=([A-Za-z_]+)")
# 处理类错误：框架已经在"处理失败"，不是业务无单。例如日期串带 Z 触发
# `unconverted data remains: Z`，这是本地解析缺陷，不能算 business。
_PROCESSING_ERROR_TYPES = frozenset({"ProcessError", "ServiceError", "WebError"})

# 响应里出现这些标识，说明上游返回的是拦截/验证页而不是业务数据。
# 与 common.md 的页面拦截规则同源，属于"官方/上游责任"的确定性证据。
_BLOCK_MARKERS = (
    "unauthorized request blocked",
    "unauthorized activity detected",
    "access denied",
    "you are a bot",
    "verifying your browser",
    "incapsula_resource",
    "captcha",
)


def exception_tail(name: str) -> str:
    """取异常类名的最后一段：``requests.exceptions.ProxyError`` -> ``ProxyError``。"""
    return (name or "").rsplit(".", 1)[-1]


def response_kind(response: str) -> str:
    """响应正文形态：json / xml / html / text / empty。"""
    text = (response or "").strip()
    if not text:
        return "empty"
    if text[0] in "{[":
        try:
            json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return "text"
        return "json"
    if text[0] == "<":
        return "xml" if text.lstrip().startswith("<?xml") else "html"
    return "text"


def classify_blame(
    *,
    message_format: str,
    exception_class: str,
    frame: str,
    kind: str,
    error: str,
    response: str = "",
) -> str:
    """按确定性证据给出缺陷归属初判。"""
    tail = exception_tail(exception_class)
    if tail in TRANSPORT_EXCEPTION_CLASSES:
        return "transport"
    if message_format == "requestResponse" and not frame:
        match = _CODE.search(error or "")
        type_match = _ERROR_TYPE.search(error or "")
        error_type = type_match.group(1) if type_match else ""
        if match and match.group(1) in _BUSINESS_CODES and error_type not in _PROCESSING_ERROR_TYPES:
            return "business"
        if error_type in _PROCESSING_ERROR_TYPES:
            # 处理类错误：需要看代码，而不是判成"无单"直接放过。
            return "code"
    # 响应命中拦截页标记：上游返回的是验证/拦截页，不是业务数据。
    if kind in {"html", "xml"} and any(marker in (response or "").casefold() for marker in _BLOCK_MARKERS):
        return "upstream"
    # 响应本身是合法 JSON，解析仍然失败 → 解析逻辑在本地（上游数据没问题）。
    if message_format == "model" and kind == "json" and "解析异常" in (error or ""):
        return "code"
    if tail == "JSONDecodeError":
        # 响应不是 JSON：多半是拦截页或官方错误页；响应是 JSON：本地解析缺陷。
        if kind and kind != "json":
            return "upstream"
        return "code" if kind == "json" else "undetermined"
    if frame:
        return "code"
    return "undetermined"


__all__ = [
    "TRANSPORT_EXCEPTION_CLASSES",
    "classify_blame",
    "exception_tail",
    "response_kind",
]
