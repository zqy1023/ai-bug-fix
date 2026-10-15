"""从模型自由文本里提取 JSON 对象。

模型经常把 JSON 放在 ```json 代码块里，或在 JSON 前后加一句说明；
直接 ``json.loads`` 会把这些正常输出判成"不是有效 JSON"，导致整个子图失败。
这里按"代码块 → 整段文本 → 首尾大括号片段"的顺序尝试，仍然失败才返回 None。
"""

from __future__ import annotations

import json
from typing import Any

_FENCE_PATTERN = r"```(?:json|JSON)?\s*(\{.*?\})\s*```"


def loads_object(text: Any) -> dict[str, Any] | None:
    """返回文本中的第一个 JSON 对象；无法解析时返回 ``None``。"""
    if not isinstance(text, str) or not text.strip():
        return None
    import re

    candidates: list[str] = [
        match.group(1) for match in re.finditer(_FENCE_PATTERN, text, re.DOTALL)
    ]
    candidates.append(text)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(payload, dict):
            return payload
    return None


__all__ = ["loads_object"]
