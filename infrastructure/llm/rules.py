"""按任务的确定性信号选择 Prompt 规则片段与预算档位。

规则片段只按"可核对的字段"选择（``blame`` / ``exception_class`` /
``response_kind`` / ``message_format`` / 正文关键词），不引入模型判断。
归属判不出来时，仍按载体格式与正文信号选片段；只有**完全没有任何信号**才退回
全量领域规则，避免因为少注入一条业务规则而改错代码。

``AI_REPAIR_PROMPT_PROFILE=full`` 使用相同核心规则与全部领域片段。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

PROFILE_ENV = "AI_REPAIR_PROMPT_PROFILE"
_PROFILES = {"slim", "full"}

# 固定顺序：决定渲染版本号，必须稳定可复现。
ALL_RULES: tuple[str, ...] = (
    "coding", "diagnosis", "framework", "notfound", "exception", "block", "legacy", "timezone",
)

# 修复方加载编码规则；双方必载业务状态规则，避免空事件等表达漏选。
_ROLE_RULES: dict[str, tuple[str, ...]] = {
    "unified_fixer": ("coding", "diagnosis", "notfound"),
    "review": ("diagnosis", "notfound"),
}

_NOTFOUND_HINTS = ("notfound", "not found", "无单", "no data", "nodata")
_TIME_HINTS = ("时间", "时区", "datetime", "timezone", "date_time", "时间戳")
_FRAMEWORK_HINTS = ("read_html", "process_html", "trackresponsemodel", "track_model")
_BLOCK_HINTS = (
    "waf", "验证码", "captcha", "blocked", "access denied", "you are a bot",
    "verifying your browser", "incapsula", "unauthorized request",
)
_EXCEPTION_HINTS = ("serviceerror", "processerror", "errortype")


def prompt_profile() -> str:
    """当前 Prompt 档位：slim（按需片段）或 full（全部片段）。"""
    value = os.environ.get(PROFILE_ENV, "").strip().casefold()
    return value if value in _PROFILES else "slim"


def _problem_text(problem: Mapping[str, Any]) -> str:
    keys = ("error", "raw_error", "biztype", "exception_message", "field", "message")
    return " ".join(str(problem.get(key) or "") for key in keys)


def select_rules(
    *,
    role: str,
    framework: str = "",
    problem: Mapping[str, Any] | None = None,
    evidence: Mapping[str, Any] | None = None,
) -> tuple[str, ...] | None:
    """返回本次领域片段；None 表示加载当前全部片段，不另用旧提示词。"""
    if prompt_profile() == "full":
        return None
    problem = problem or {}
    evidence = evidence or {}
    base = list(_ROLE_RULES.get(role, ()))
    blame = str(problem.get("blame") or "")
    text = _problem_text(problem)
    lower = text.casefold()
    kind = str(problem.get("response_kind") or "")
    message_format = str(problem.get("message_format") or "")

    selected = list(base)
    if kind in {"html", "xml"} or any(hint in lower for hint in _BLOCK_HINTS):
        selected.append("block")
    if str(problem.get("exception_class") or "") or any(h in lower for h in _EXCEPTION_HINTS):
        selected.append("exception")
    if any(hint in lower for hint in _NOTFOUND_HINTS):
        selected.append("notfound")
    if any(hint in lower for hint in _TIME_HINTS):
        selected.append("timezone")
    # 解析类日志天然涉及框架接口（read_html/process_html/TrackResponseModel）。
    if message_format in {"model", "trace"} or any(hint in lower for hint in _FRAMEWORK_HINTS):
        selected.append("framework")
    if framework == "spiders" or evidence.get("legacy_dependency") or evidence.get("legacy_only"):
        selected.append("legacy")
    ordered = [rule for rule in ALL_RULES if rule in set(selected)]
    # 归属判不出来且正文毫无领域信号时，退回全量规则——宁可多花 token 也不漏业务规则。
    if blame in {"", "undetermined"} and not (set(ordered) - set(base)):
        return ALL_RULES
    return tuple(ordered)


@dataclass(frozen=True)
class BudgetTier:
    """单次调用的预算档位。"""

    name: str
    max_turns: int
    max_tool_calls: int
    max_context_chars: int
    timeout_seconds: int


TIERS: dict[str, BudgetTier] = {
    "analysis": BudgetTier("analysis", max_turns=12, max_tool_calls=16,
                           max_context_chars=24000, timeout_seconds=300),
    # 单点可定位且有原始响应：预留源码核对、修改与验证空间。
    "small": BudgetTier("small", max_turns=16, max_tool_calls=24,
                        max_context_chars=30000, timeout_seconds=480),
    "default": BudgetTier("default", max_turns=24, max_tool_calls=40,
                          max_context_chars=60000, timeout_seconds=600),
    # 无栈帧、无响应或多候选：给予更完整的探索空间。
    "complex": BudgetTier("complex", max_turns=32, max_tool_calls=64,
                          max_context_chars=120000, timeout_seconds=900),
}


def budget_tier(
    *,
    blame: str,
    frame: str,
    has_response: bool,
    candidate_count: int,
    field: str = "",
    analysis_only: bool = False,
) -> BudgetTier:
    """按证据充分度选预算：证据越足，轮次和上下文越小。

    ``field``（业务字段路径，如 ``track_result_events.datetime``）不是代码位置，
    但能把搜索范围压到具体模型/字段，因此单独成档；只有"既无栈帧也无字段线索"
    才落到 complex。
    """
    if analysis_only:
        return TIERS["analysis"]
    if blame == "code" and frame and has_response and candidate_count <= 2:
        return TIERS["small"]
    if (blame in {"code", "upstream"} and frame) or field:
        return TIERS["default"]
    return TIERS["complex"]


__all__ = ["ALL_RULES", "BudgetTier", "PROFILE_ENV", "TIERS", "budget_tier",
           "prompt_profile", "select_rules"]
