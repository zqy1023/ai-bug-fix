"""修复 agent 的输出契约。

提示词要求模型只输出 passed / analysis / changed_files / summary / change_plan；
这里只做结构校验、业务判定和"可持久化视图"的裁剪，不涉及任何 provider 或主图概念。

关键约定：``passed=false`` **不再清空分析**。根因落在共享层时，``change_plan``
原样保留并标记 ``scope_escalation_required``，供终态报告记录分析结果。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.application.plans import plan_of

REQUIRED_FIELDS = ("passed", "analysis", "changed_files", "summary")
# 需要跟着任务状态长期携带的字段
PLAN_FIELDS = ("passed", "analysis", "changed_files", "summary", "change_plan")
_MAX_FIELD_CHARS = 4000


@dataclass(frozen=True)
class RepairVerdict:
    """输出判定：结构是否合法、修复是否可用于后续门禁。"""

    valid: bool
    passed: bool
    reason: str = ""


def validate_shape(payload: dict[str, Any]) -> tuple[bool, str]:
    """校验修复输出的结构是否完整。"""
    missing = [field for field in REQUIRED_FIELDS if field not in payload]
    if missing:
        return False, f"修复输出缺少字段: {', '.join(missing)}"
    files = payload.get("changed_files")
    if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
        return False, "changed_files 必须是字符串数组"
    if not isinstance(payload.get("analysis"), str):
        return False, "analysis 必须是字符串"
    if not isinstance(payload.get("summary"), str):
        return False, "summary 必须是字符串"
    if not isinstance(payload.get("passed"), bool):
        return False, "passed 必须是布尔值"
    return True, ""


def changed_files_of(payload: dict[str, Any]) -> list[str]:
    """返回修改清单，结构非法时返回空列表。"""
    files = payload.get("changed_files")
    if not isinstance(files, list):
        return []
    return [str(item) for item in files]


def change_plan_of(payload: dict[str, Any]) -> dict[str, Any]:
    """返回结构化方案。模型没给或给的不合法时返回空字典。

    方案与 ``passed`` 无关：判定不通过时它照样要活下来，这正是"给了方案却说无法
    修改"的反义词。
    """
    plan = plan_of(payload)
    return plan.model_dump(mode="json") if plan is not None else {}


def needs_escalation(payload: dict[str, Any], *, passed: bool) -> bool:
    """方案影响共享层且本轮未完成修改时，转分析报告。"""
    if passed:
        return False
    raw_plan = payload.get("change_plan")
    if not isinstance(raw_plan, dict):
        return False
    # 共享层方案即使只有理由、没有 edits，也必须进入分析报告流程。
    scope = raw_plan.get("scope")
    return isinstance(scope, str) and scope.casefold() == "project"


def _slim_value(value: Any) -> Any:
    """递归裁剪方案内容，避免完整文件内容撑大任务状态。"""
    if isinstance(value, str):
        return _clip_field(value)
    if isinstance(value, list):
        return [_slim_value(item) for item in value[:20]]
    if isinstance(value, dict):
        return {key: _slim_value(item) for key, item in value.items()}
    return value




def blocked_reason_of(payload: dict[str, Any], verdict: RepairVerdict) -> str:
    """改不动的结构化原因：优先用方案理由，其次用判定理由。"""
    if verdict.passed:
        return ""
    plan = plan_of(payload)
    if plan is not None:
        return plan.rationale or "根因超出目标 carrier 范围，已产出待授权方案"
    return verdict.reason


def slim_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """裁剪成可持久化的修复结论视图。

    provider 的原始返回里还有提示词、消息事件等大件；把它们反复写进任务状态会让
    ``task.json`` 和后续每个节点的 ``input.json`` 逐轮膨胀。这里只保留
    passed / analysis / summary 与修改清单。
    """
    slim: dict[str, Any] = {}
    for field in PLAN_FIELDS:
        if field not in payload:
            continue
        value = payload[field]
        if field == "change_plan" and isinstance(value, dict):
            slim[field] = _slim_value(value)
        else:
            slim[field] = value if not isinstance(value, str) else _clip_field(value)
    slim["changed_files"] = changed_files_of(payload)
    return slim


def _clip_field(value: str) -> str:
    """超长结论字段截断，避免单条记录把状态文件撑大。"""
    return value if len(value) <= _MAX_FIELD_CHARS else f"{value[:_MAX_FIELD_CHARS]}…（已截断）"


def judge(payload: dict[str, Any]) -> RepairVerdict:
    """结构必须完整；若 passed=true 则必须给出修改清单。"""
    valid, reason = validate_shape(payload)
    if not valid:
        return RepairVerdict(False, False, reason)
    if payload.get("passed") is not True:
        return RepairVerdict(True, False, "修复未产生有效修改（passed=false）")
    if not changed_files_of(payload):
        return RepairVerdict(True, False, "changed_files 为空，无法进入门禁")
    return RepairVerdict(True, True, "修复输出有效")


__all__ = [
    "PLAN_FIELDS",
    "REQUIRED_FIELDS",
    "RepairVerdict",
    "blocked_reason_of",
    "change_plan_of",
    "changed_files_of",
    "judge",
    "needs_escalation",
    "slim_plan",
    "validate_shape",
]
