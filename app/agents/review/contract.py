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
        if field == "change_plan" and isinstance(value, dict):
            slim[field] = _clip_nested(value)
        elif isinstance(value, str):
            slim[field] = _clip(value)
        elif isinstance(value, list):
            slim[field] = _cap_items(value)
        else:
            slim[field] = value
    return slim


def _clip(value: str) -> str:
    """超长文本截断，避免单条记录把状态文件撑大。"""
    return value if len(value) <= _MAX_FIELD_CHARS else f"{value[:_MAX_FIELD_CHARS]}…（已截断）"


def _clip_nested(value: Any) -> Any:
    """递归裁剪结构化方案，避免完整文件内容撑大任务状态。"""
    if isinstance(value, str):
        return _clip(value)
    if isinstance(value, list):
        return [_clip_nested(item) for item in value[:_MAX_ITEMS]]
    if isinstance(value, dict):
        return {key: _clip_nested(item) for key, item in value.items()}
    return value


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
    """判定审查输出。

    MANUAL 表示 AI 无法独立关闭审查，需转最终人工审批；它按可交付处理，
    不等于“只提交分析报告”。主图必须先提交源码分支，成功后才允许挂起审批。
    PASS 通过；FAIL 未通过并进入有界回修。
    """
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
