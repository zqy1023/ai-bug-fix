"""审查 agent 的输出契约。

只读审查只输出结论、公开发现项和回修要求；本文件不涉及 provider 与主图。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RESULT_VALUES = ("PASS", "FAIL", "MANUAL")
# 需要跟着任务状态长期携带的字段：结论、公开发现项与回修要求。
RESULT_FIELDS = ("result", "passed", "summary", "findings", "required_changes", "change_plan")
_MAX_FIELD_CHARS = 4000
_MAX_ITEMS = 20


def slim_review(payload: dict[str, Any]) -> dict[str, Any]:
    """裁剪成可持久化的审查结论视图，去掉无需长期携带的原始返回。"""
    slim: dict[str, Any] = {}
    for field in RESULT_FIELDS:
        if field not in payload:
            continue
        value = payload[field]
        if isinstance(value, str):
            slim[field] = _clip(value)
        elif isinstance(value, list):
            slim[field] = _cap_items(value)
        else:
            slim[field] = value
    return slim


def _clip(value: str) -> str:
    """超长文本截断，避免单条记录把状态文件撑大。"""
    return value if len(value) <= _MAX_FIELD_CHARS else f"{value[:_MAX_FIELD_CHARS]}…（已截断）"


def _cap_items(items: list[Any]) -> list[Any]:
    """清单类字段限制条数与每条字数。"""
    capped: list[Any] = []
    for item in items[:_MAX_ITEMS]:
        if isinstance(item, str):
            capped.append(_clip(item))
        elif isinstance(item, dict):
            capped.append(
                {
                    str(key): (_clip(str(value)) if isinstance(value, str) else value)
                    for key, value in list(item.items())[:8]
                }
            )
        else:
            capped.append(item)
    return capped


@dataclass(frozen=True)
class ReviewVerdict:
    """输出判定：结构是否合法、审查是否通过。"""

    valid: bool
    passed: bool
    reason: str = ""


def result_of(payload: dict[str, Any]) -> str:
    """返回大写结论，缺失时为空串。"""
    value = payload.get("result")
    return value.upper().strip() if isinstance(value, str) else ""


def validate_shape(payload: dict[str, Any]) -> tuple[bool, str]:
    """校验审查输出的结构：结论、布尔值和两个清单。"""
    value = result_of(payload)
    if value not in RESULT_VALUES:
        return False, f"result 必须是 {'/'.join(RESULT_VALUES)}"
    if not isinstance(payload.get("passed"), bool):
        return False, "passed 必须是布尔值"
    if value != "MANUAL" and (value == "PASS") != payload["passed"]:
        return False, "passed 必须与 result 一致"
    if not isinstance(payload.get("findings"), list):
        return False, "findings 必须是数组"
    if not isinstance(payload.get("required_changes"), list):
        return False, "required_changes 必须是数组"
    return True, ""


def judge(payload: dict[str, Any]) -> ReviewVerdict:
    """MANUAL 表示转人工审批（按通过处理），PASS 通过，FAIL 未通过。"""
    valid, reason = validate_shape(payload)
    if not valid:
        return ReviewVerdict(False, False, reason)
    value = result_of(payload)
    if value == "MANUAL":
        return ReviewVerdict(True, True, "审查 agent 要求转人工审查")
    if value != "PASS":
        return ReviewVerdict(True, False, f"审查未通过: {value}")
    return ReviewVerdict(True, True, "审查通过")


__all__ = [
    "RESULT_FIELDS",
    "RESULT_VALUES",
    "ReviewVerdict",
    "judge",
    "result_of",
    "slim_review",
    "validate_shape",
]
