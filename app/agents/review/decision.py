"""确定性证据约束审查结论；AI 只能解释业务含义，不能覆盖失败或补造证据。"""

from __future__ import annotations

from typing import Any

from app.workflow.steps.verify_issue.assessment import verification_is_complete, verification_for_review


def evidence_constraints(state: dict[str, Any], coverage: dict[str, Any]) -> tuple[list[str], list[str]]:
    """返回已确认失败与证据缺口，分别驱动 FAIL 和 MANUAL。

    Args:
        state: 当前补丁的门禁、问题验证和补充命令事实。
        coverage: 模型针对变更函数给出的覆盖判断。

    Returns:
        失败项和缺口项；缺少额外正常样本不会被自动认作代码缺陷。
    """
    failures: list[str] = []
    gaps: list[str] = []
    requirements = ((state.get("regression_result") or {}).get("validation_requirements") or
                    (state.get("verify_result") or {}).get("validation_requirements") or [])
    from app.workflow.validation_requirements import assess
    requirements = assess(requirements, {"requirement_results": [r.get("evidence") or {} for r in requirements]},
                          (state.get("verify_result") or {}).get("evidence_version") or {})
    for requirement in requirements:
        message = f"{requirement.get('path')}:{requirement.get('symbol')} — {requirement.get('requirement')}"
        if requirement.get("status") == "failed":
            failures.append("变更验证要求明确失败：" + message)
        elif requirement.get("status") != "passed":
            gaps.append("变更验证要求未取得执行证据：" + message)
    from app.agents.review.boundaries import datetime_reset_risks, not_found_guard_changes
    gaps.extend(not_found_guard_changes(str(state.get("ai_workspace_path") or ""),
                                       str(state.get("baseline_dir") or ""), list(state.get("changed_files") or [])))
    gaps.extend(datetime_reset_risks(str(state.get("ai_workspace_path") or ""),
                                   str(state.get("baseline_dir") or ""), list(state.get("changed_files") or [])))
    for key, label in (("gate_result", "变更检查"), ("verify_result", "原始问题验证"),
                       ("regression_result", "补充回归命令")):
        value = verification_for_review(state) if key == "verify_result" else state.get(key) or {}
        if value.get("status") == "failed" or (key == "gate_result" and value.get("passed") is False):
            failures.append(f"{label}明确失败：{value.get('reason') or '查看该步骤失败证据'}")
        if not (value.get("evidence_version") or {}).get("patch_id"):
            gaps.append(f"重新执行{label}，生成与当前补丁绑定的证据")
    if not state.get("check_passed"):
        gaps.append("补齐已通过的变更检查")
    if not state.get("syntax_passed"):
        gaps.append("补齐已通过的语法检查")
    verify = verification_for_review(state)
    facts = verify.get("verification") or {}
    if not verification_is_complete(verify):
        gaps.append(f"重跑原始问题验证：{verify.get('reason') or '缺少输入送达、基线复现及修复后断言证据'}")
    functions = facts.get("changed_functions") or []
    if coverage.get("decision") != "sufficient" or not str(coverage.get("reason") or "").strip():
        gaps.append("明确本次变更的覆盖依据和必需验证缺口")
    if not functions or not set(functions).issubset(set(coverage.get("reviewed_functions") or [])):
        gaps.append("逐项核对验证报告中的 changed_functions，并说明每项变更的验证依据")
    gaps.extend(str(value) for value in coverage.get("missing_checks") or [])
    regression = state.get("regression_result") or {}
    status = regression.get("status")
    if status not in {"verified", "syntax_only", "not_configured", "not_executed", "skipped"}:
        gaps.append(f"核对补充命令执行状态：{regression.get('reason') or '缺少执行记录'}")
    # syntax_only/无配置只描述执行事实。能否无需额外样本，由上面的逐函数
    # 覆盖评估和已完成的问题验证共同决定，不再凭状态字符串自动豁免。
    return list(dict.fromkeys(failures)), list(dict.fromkeys(gaps))
