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


def normalize_error(error: str) -> str:
    """移除行列号与对象地址等位置噪声，保留异常语义和真实时间值。"""
    value = _LINE_COLUMN.sub("", error or "")
    value = _OBJECT_ADDRESS.sub("<object>", value)
    return re.sub(r"\s+", " ", value).strip()


__all__ = ["normalize_error"]
