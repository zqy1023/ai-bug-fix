"""由当前补丁的最终证据统一计算人工确认义务。"""

from __future__ import annotations

from typing import Any


def confirmation(state: dict[str, Any]) -> tuple[bool, list[str]]:
    """每次从当前结果重算，既不丢失后续缺口，也不保留已消除的旧缺口。"""
    reasons: list[str] = []
    from app.agents.review.contract import REVIEW_CONTRACT_VERSION
    review = state.get("review") or {}
    verdict = str(review.get("verdict") or "").upper()
    if verdict == "PASS" and review.get("review_contract_version") != REVIEW_CONTRACT_VERSION:
        reasons.append("旧版 AI 审查未采用当前业务错误及边界规则，需要重新审查或人工确认")
    if verdict in {"MANUAL", "HUMAN"} or (not verdict and state.get("review_mode") == "human"):
        reasons.append(str(review.get("summary") or "本次候选需要人工审查确认"))
        reasons.extend(str(value) for value in review.get("required_changes") or [])
    for key, label in (("verify_result", "原始问题验证"), ("regression_result", "业务回归")):
        result = state.get(key) or {}
        if key == "verify_result":
            from app.workflow.steps.verify_issue.assessment import (
                verification_for_review,
                verification_is_complete,
            )
            result = verification_for_review(state)
            if result.get("status") == "verified" and not verification_is_complete(result):
                reasons.append("业务验证不符合当前证据契约，需要重新验证或人工确认")
        if result.get("status") in {"unverified", "failed"}:
            reasons.append(f"{label}：{result.get('reason') or result['status']}")
    # 未进入验证的只读/历史流程仍保留显式人工要求。
    if not any(state.get(key) for key in ("review", "verify_result", "regression_result")):
        reasons.extend(str(value) for value in state.get("manual_confirmation_reasons") or [])
        if state.get("manual_confirmation_required") and not reasons:
            reasons.append("当前任务明确要求人工确认")
    reasons = list(dict.fromkeys(value for value in reasons if value.strip()))
    return bool(reasons), reasons
