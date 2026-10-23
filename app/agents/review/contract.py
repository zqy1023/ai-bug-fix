"""审查 agent 的输出契约。

只读审查只输出结论、公开发现项和回修要求；本文件不涉及 provider 与主图。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RESULT_VALUES = ("PASS", "FAIL", "MANUAL")
REVIEW_CONTRACT_VERSION = 6
# 需要跟着任务状态长期携带的字段：结论、公开发现项与回修要求。
RESULT_FIELDS = ("verdict", "passed", "code_verdict", "coverage", "summary", "findings", "required_changes", "change_plan", "acceptance_results")
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
            from copy import deepcopy
            slim[field] = deepcopy(value)
        elif field == "coverage" and isinstance(value, dict):
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
    value = payload.get("verdict")
    return value.upper().strip() if isinstance(value, str) else ""


def validate_shape(payload: dict[str, Any]) -> tuple[bool, str]:
    """校验审查输出的结构：结论、布尔值和两个清单。"""
    value = result_of(payload)
    if "result" in payload:
        return False, "审查协议不接受 result 字段，必须使用 verdict"
    if value not in RESULT_VALUES:
        return False, f"verdict 必须是 {'/'.join(RESULT_VALUES)}"
    if not isinstance(payload.get("passed"), bool):
        return False, "passed 必须是布尔值"
    if (value == "PASS") != payload["passed"]:
        return False, "passed 必须与 result 一致"
    if not isinstance(payload.get("findings"), list):
        return False, "findings 必须是数组"
    if not isinstance(payload.get("required_changes"), list):
        return False, "required_changes 必须是数组"
    if payload.get("code_verdict") not in {"PASS", "FAIL", "UNKNOWN"}:
        return False, "code_verdict 必须独立声明 PASS/FAIL/UNKNOWN"
    if value == "PASS" and payload["code_verdict"] != "PASS":
        return False, "批准补丁必须先明确代码审查通过"
    for field in ("findings", "required_changes"):
        if any(not isinstance(item, str) or not item.strip() for item in payload[field]):
            return False, f"{field} 只能包含非空字符串"
    if payload["code_verdict"] == "FAIL" and not payload["findings"]:
        return False, "代码缺陷结论必须提供具体 findings"
    if payload["code_verdict"] == "PASS" and payload["findings"]:
        return False, "已确认缺陷尚存在时不能声明代码检查通过"
    if value == "PASS" and payload["required_changes"]:
        return False, "仍有必需处理项时不能批准补丁"
    coverage = payload.get("coverage")
    if not isinstance(coverage, dict) or coverage.get("decision") not in {"sufficient", "insufficient", "unknown"}:
        return False, "coverage 必须明确声明 sufficient/insufficient/unknown"
    if not isinstance(coverage.get("reason"), str) or not coverage["reason"].strip():
        return False, "coverage 必须说明针对本次变更的覆盖依据或证据缺口"
    for field in ("reviewed_symbols", "missing_checks"):
        if not isinstance(coverage.get(field), list) or any(not isinstance(item, str) for item in coverage[field]):
            return False, f"coverage.{field} 必须是字符串数组"
    if value == "PASS" and coverage["decision"] != "sufficient":
        return False, "代码审查通过必须明确实际变更覆盖充分"
    if coverage["decision"] == "sufficient" and coverage["missing_checks"]:
        return False, "覆盖仍有必需检查缺口时不能声明 sufficient"
    if payload.get("change_plan"):
        from app.application.plans import ChangePlan
        from pydantic import ValidationError
        try:
            ChangePlan.model_validate(payload["change_plan"])
        except ValidationError as error:
            return False, f"change_plan 协议错误：{error}"
    return True, ""


def judge(payload: dict[str, Any]) -> ReviewVerdict:
    """判定审查输出。

    passed 只表示批准；MANUAL 合法但未批准。候选交付资格由图中的
    delivery_eligible 单独决定，不能再把“可提交候选”写成“审查通过”。
    """
    valid, reason = validate_shape(payload)
    if not valid:
        return ReviewVerdict(False, False, reason)
    value = result_of(payload)
    if value == "MANUAL":
        return ReviewVerdict(True, False, "审查 agent 要求转人工审查")
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
