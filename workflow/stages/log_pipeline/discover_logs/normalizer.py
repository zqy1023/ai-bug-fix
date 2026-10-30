"""异常日志标准化、去重和按 ``biztype + carrier`` 分组。

标准化只从原始字段取值，不根据异常文本或目录名猜测 ``service_name``、
``biztype``、``carrier``。时间戳属于业务证据（能暴露解析格式），保留原值；
去重键在 ``deduplicate_error`` 中单独对时间值做规范化，保证聚合稳定。
"""

from __future__ import annotations

import re

_LINE_COLUMN = re.compile(
    r"line \d+ column \d+ \(char \d+\)|line \d+, column \d+|, line: \d+|, column \d+"
)
_OBJECT_ADDRESS = re.compile(r"<[^>]*0x[0-9a-fA-F]+>")
_FILE_FRAME = re.compile(
    r"file:\s*(?P<file>[^,\s]+)\s*,\s*line:\s*(?P<line>\d+)\s*,\s*function:\s*(?P<function>[^,\s]+)"
)
_EXCEPTION_NAME = re.compile(r"[A-Za-z_][\w.]*")
# 异常类可能出现在行中间：没有 ` error:` 标记时 error 会退化成整行日志
# （以时间戳开头），只在开头匹配会漏掉 `builtins.IndexError` 这类真实类名。
# 只认异常后缀，避免把 `framework.track_server` 这类业务主题当成类名。
_EXCEPTION_TOKEN = re.compile(
    r"[A-Za-z_][\w.]*(?:Error|Exception|Timeout|Warning|Interrupt|Exit)"
)
# 日志里的业务字段路径，例如 `key:track_result_events.datetime`。
_FIELD_KEY = re.compile(r"\bkey\s*[:=]\s*([A-Za-z0-9_.\[\]]+)")


def error_field(error: str) -> str:
    """提取异常指向的业务字段路径。

    这不是代码位置（不是 ``file:line``），但能大幅缩小搜索范围：时间字段的问题
    基本就落在 DateTimeModel / TrackResultEventModel 附近。有它就可以把预算从
    complex 降到 default，而不用凭空断言归属。
    """
    match = _FIELD_KEY.search(error or "")
    return match.group(1) if match else ""


def normalize_error(error: str) -> str:
    """移除行列号与对象地址等位置噪声，保留异常语义和真实时间值。"""
    value = _LINE_COLUMN.sub("", error or "")
    value = _OBJECT_ADDRESS.sub("<object>", value)
    return re.sub(r"\s+", " ", value).strip()


def error_parts(error: str) -> tuple[str, str, str]:
    """把异常文本拆成 ``(类名, 消息, 位置)``。

    位置形如 ``file: tpcindia.py, line: 41, function: read_html``，统一表示成
    ``文件:行号:函数``；没有位置信息时返回空串。

    结构化之后，清洗规则可以按异常类做精确匹配，而不是在整段文本里搜子串——
    子串匹配会把消息正文里出现的 "ProxyError" 也当成环境问题误杀。
    """
    text = (error or "").strip()
    frame = ""
    match = _FILE_FRAME.search(text)
    if match:
        frame = f"{match.group('file')}:{match.group('line')}:{match.group('function')}"
        text = f"{text[:match.start()]} {text[match.end():]}".strip()
    head = _EXCEPTION_TOKEN.search(text)
    name = head.group(0) if head else ""
    message = text[len(name):].lstrip(" ,") if name and text.startswith(name) else text
    return name, re.sub(r"\s+", " ", message).strip(" ,"), frame


__all__ = ["error_field", "error_parts", "normalize_error"]
