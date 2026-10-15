"""异常日志标准化、去重和按 ``biztype + carrier`` 分组。

标准化只从原始字段取值，不根据异常文本或目录名猜测 ``service_name``、
``biztype``、``carrier``。
"""

from __future__ import annotations

import re

_LINE_COLUMN = re.compile(
    r"line \d+ column \d+ \(char \d+\)|line \d+, column \d+|, line: \d+|, column \d+"
)
_OBJECT_ADDRESS = re.compile(r"<[^>]*0x[0-9a-fA-F]+>")
_TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b")


def normalize_error(error: str) -> str:
    """移除行列号等位置噪声，保留可用于问题聚合的异常语义。"""
    value = _LINE_COLUMN.sub("", error or "")
    value = _OBJECT_ADDRESS.sub("<object>", value)
    value = _TIMESTAMP.sub("<timestamp>", value)
    return re.sub(r"\s+", " ", value).strip()


__all__ = ["normalize_error"]
