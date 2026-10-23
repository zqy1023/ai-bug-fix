"""验证工作流的唯一契约：计划、事实和动作分开，不接受模型自定验收门槛。"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal
from app.workflow.spec import FrozenModel

from app.workflow.evidence_version import evidence_version
from app.workflow.steps.verify_issue.assessment import changed_functions, verification_is_complete

WORKFLOW_VERSION = 5
PLAN = "plan_validation"
SUMMARY = "summarize_validation"
DECIDE = "decide_validation"
RECOVERY_TARGETS = {"validate_changes", "verify_issue", "run_regression", "review_agent"}


class ValidationPlan(FrozenModel):
    workflow_version: Literal[5]
    plan_id: str
    evidence_version: dict[str, Any]
    gate_policy_id: str
    changed_files: list[str]
    changed_symbols: list[str]
    input_id: str
    has_original_response: bool
    original_error: str
    issue_required: Literal[True]
    request_required: Literal[True]
    sample_policy: Literal["original_recording_and_actual_request"]
    acceptance: list[str]
    review_mode: Literal["agent", "human"]


class VerificationFact(FrozenModel):
    status: Literal["passed", "failed", "unverified"]
    reason: str
    retryable: bool
    report_path: str


class ValidationSummary(FrozenModel):
    workflow_version: Literal[5]
    plan_id: str
    evidence_version: dict[str, Any]
    stale: bool
    dimensions: dict[str, VerificationFact]
    changed_symbols: list[str]


class ValidationDecision(FrozenModel):
    workflow_version: Literal[5]
    plan_id: str | None
    action: Literal["retry", "repair", "deliver", "blocked"]
    target: Literal["", "repair_agent", "validate_changes", "verify_issue", "run_regression", "review_agent"]
    reason: str
    manual_required: bool
    delivery_eligible: bool
    recovery_counts: dict[str, int]
    repair_attempt: int


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def make_plan(state: dict[str, Any]) -> dict[str, Any]:
    from pathlib import Path
    from app.workflow.steps.check_changes import verification
    problem = dict(state.get("problem") or {})
    request = problem.get("req") or {}
    if isinstance(request, str):
        try:
            request = json.loads(request)
        except ValueError:
            request = {}
    body = problem.get("response")
    if body is None and isinstance(request, dict):
        body = request.get("response")
    files = list(state.get("changed_files") or [])
    version = evidence_version(state["ai_workspace_path"], files, state["baseline_dir"])
    if not state.get("check_passed") or not state.get("syntax_passed"):
        raise ValueError("范围与语法检查尚未通过，不能建立验证计划")
    functions = changed_functions(Path(state["ai_workspace_path"]), Path(state["baseline_dir"]), files)
    plan = {"workflow_version": WORKFLOW_VERSION, "evidence_version": version,
            "gate_policy_id": hashlib.sha256(Path(verification.__file__).read_bytes()).hexdigest(),
            "changed_files": files, "changed_symbols": functions,
            "input_id": fingerprint(problem), "has_original_response": bool(body),
            "original_error": str(problem.get("error") or problem.get("raw_error") or ""),
            "issue_required": True, "request_required": True,
            "sample_policy": "original_recording_and_actual_request",
            "acceptance": ["原始异常在基线复现且修复后符合框架协议", "原请求通过 server_dev.main 执行并核对实际结果"],
            "review_mode": state.get("review_mode", "agent")}
    plan["plan_id"] = fingerprint(plan)
    return ValidationPlan.model_validate(plan).model_dump(mode="json")


def summarize(state: dict[str, Any]) -> dict[str, Any]:
    from pathlib import Path
    from app.workflow.steps.check_changes import verification
    plan = state.get("validation_plan") or {}
    if plan.get("workflow_version") != WORKFLOW_VERSION:
        raise ValueError("验证计划版本无效，必须重新运行当前工作流")
    version = plan["evidence_version"]
    current = evidence_version(state["ai_workspace_path"], state.get("changed_files") or [], state["baseline_dir"])
    stale = (plan.get("plan_id") != fingerprint({k: v for k, v in plan.items() if k != "plan_id"})
             or current != version or fingerprint(state.get("problem") or {}) != plan["input_id"]
             or not state.get("check_passed") or not state.get("syntax_passed")
             or (state.get("gate_result") or {}).get("evidence_version", {}).get("patch_id") != version["patch_id"])
    stale = (stale or plan["gate_policy_id"] != hashlib.sha256(Path(verification.__file__).read_bytes()).hexdigest()
             or (state.get("gate_result") or {}).get("evidence_version", {}).get("policy_id") != plan["gate_policy_id"])
    dimensions = {}
    for name, key in (("issue", "verify_result"), ("request", "regression_result")):
        report = state.get(key) or {}
        if (report.get("evidence_version") or {}).get("patch_id") != version["patch_id"]:
            stale = True
        if report.get("status") == "verified" and report.get("input_id") != plan["input_id"]:
            stale = True
        if name == "issue" and report.get("status") == "verified" and (report.get("evidence_version") or {}).get("baseline_id") != version["baseline_id"]:
            stale = True
        if name == "issue":
            passed = verification_is_complete(report) and plan["has_original_response"] and bool(plan["original_error"])
            unavailable = not plan["has_original_response"] or not plan["original_error"]
        else:
            actual = report.get("request_regression") or {}
            passed = (report.get("passed") is True and report.get("status") == "verified"
                      and actual.get("passed") is True and actual.get("status") == "verified"
                      and actual.get("entrypoint") == "server_dev.main")
            unavailable = (report.get("status") in {"not_configured", "not_executed", "syntax_only"}
                           or report.get("failure_kind") == "policy"
                           or (not actual and report.get("failure_kind") != "execution"))
        status = "passed" if passed else "failed" if report.get("status") == "failed" else "unverified"
        dimensions[name] = {"status": status, "reason": str(report.get("reason") or "未取得有效执行结果"),
                            "retryable": status == "unverified" and not unavailable,
                            "report_path": report.get("report_path", "")}
    return ValidationSummary.model_validate({"workflow_version": WORKFLOW_VERSION, "plan_id": plan["plan_id"],
            "evidence_version": version, "stale": stale, "dimensions": dimensions,
            "changed_symbols": plan["changed_symbols"]}).model_dump(mode="json")


def decide(state: dict[str, Any]) -> dict[str, Any]:
    summary = state.get("validation_summary") or {}
    if state.get("validation_plan"):
        summary = summarize(state)
    review = state.get("review") or {}
    attempts = dict(state.get("validation_recovery_counts") or {})
    repair_attempt = int(state.get("automatic_attempt") or 1)
    limit = max(1, int(state.get("max_attempts") or 3))
    target, reason, action = "", "", "blocked"
    human = state.get("review_mode") == "human"
    dimensions = summary.get("dimensions") or {}
    if (summary.get("workflow_version") != WORKFLOW_VERSION or summary.get("stale")
        or set(dimensions) != {"issue", "request"}):
        target, reason = "validate_changes", "源码或验证输入已变化，重新检查并建立验证计划"
    elif any(item.get("status") == "failed" for item in dimensions.values()):
        action, target, reason = "repair", "repair_agent", "实际业务验证发现失败，回修代码"
    else:
        pending = [(key, item) for key, item in dimensions.items() if item.get("status") != "passed"]
        if pending:
            key, item = pending[0]
            target = ("verify_issue" if key == "issue" else "run_regression") if item.get("retryable") else ""
            reason = item["reason"]
        elif not human and (review.get("contract_error") or review.get("code_verdict") == "UNKNOWN" or not review
                            or review.get("review_contract_version") != WORKFLOW_VERSION):
            target, reason = "review_agent", str(review.get("contract_error") or "审查未取得明确的代码结论")
        elif not human and review.get("code_verdict") == "FAIL":
            action, target, reason = "repair", "repair_agent", str(review.get("summary") or "代码审查发现缺陷")
        elif human or (review.get("verdict") == "PASS" and review.get("code_verdict") == "PASS"
                       and (review.get("evidence_version") or {}).get("patch_id") ==
                       (summary.get("evidence_version") or {}).get("patch_id")):
            action, reason = "deliver", "验证完成，进入交付及最终审批"
        else:
            reason = str(review.get("summary") or "存在需要业务判断的具体问题")
    if target in RECOVERY_TARGETS:
        if attempts.get(target, 0) < limit - 1:
            action = "retry"
        else:
            action, target, reason = "blocked", "", f"自动恢复已达上限：{reason}"
    if action == "repair" and repair_attempt >= limit:
        action, target, reason = "blocked", "", f"代码回修已达上限：{reason}"
    return ValidationDecision.model_validate({"workflow_version": WORKFLOW_VERSION, "plan_id": summary.get("plan_id"),
            "action": action, "target": target, "reason": reason,
            "manual_required": human and action == "deliver", "delivery_eligible": action == "deliver",
            "recovery_counts": attempts, "repair_attempt": repair_attempt}).model_dump(mode="json")


def decision_patch(state: dict[str, Any]) -> dict[str, Any]:
    decision = decide(state)
    return {"validation_decision": decision, "retry_target": decision["target"],
            "retry_reason": decision["reason"], "blocked_reason": decision["reason"] if decision["action"] == "blocked" else "",
            "manual_confirmation_required": decision["manual_required"],
            "manual_confirmation_reasons": ["用户选择人工代码审查"] if decision["manual_required"] else [],
            "terminal_status": "BLOCKED" if decision["action"] == "blocked" else "passed",
            "last_result": "passed"}


def assert_workflow_version(metadata: dict[str, Any]) -> None:
    if metadata.get("workflow_version") != WORKFLOW_VERSION:
        raise ValueError("旧版本工作流不支持继续执行，请新建任务；历史记录仅供查看")
