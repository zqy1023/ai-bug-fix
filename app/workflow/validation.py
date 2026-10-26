"""验证工作流的唯一契约：计划、事实和动作分开，不接受模型自定验收门槛。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from app.agents.review.contract import REVIEW_CONTRACT_VERSION
from app.workflow.contracts import (
    WORKFLOW_VERSION,
    AssertionResult,
    EvidenceBinding,
    ValidationReport,
)
from app.workflow.evidence_version import evidence_version
from app.workflow.spec import FrozenModel
from app.agents.review.tools.verify_issue.assessment import (
    changed_functions,
    verification_is_complete,
)
from config.settings import settings

PLAN = "plan_validation"
SUMMARY = "summarize_validation"
DECIDE = "decide_validation"
RECOVERY_TARGETS = {
    "review_agent",
}


class ValidationPlan(FrozenModel):
    workflow_version: Literal[7]
    plan_id: str
    binding: EvidenceBinding
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
    workflow_version: Literal[7]
    plan_id: str
    evidence_version: dict[str, Any]
    stale: bool
    dimensions: dict[str, VerificationFact]
    changed_symbols: list[str]
    report: ValidationReport


class ValidationDecision(FrozenModel):
    workflow_version: Literal[7]
    plan_id: str | None
    action: Literal["retry", "repair", "deliver", "blocked"]
    target: Literal[
        "",
        "repair_agent",
                    "review_agent",
    ]
    reason: str
    manual_required: bool
    delivery_eligible: bool
    recovery_counts: dict[str, int]
    repair_attempt: int


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def make_plan(state: dict[str, Any]) -> dict[str, Any]:
    from pathlib import Path

    from app.agents.review.tools.check_changes import verification

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
    if not (state.get("gate_result") or {}).get("passed") or not (
        state.get("syntax_result") or {}
    ).get("passed"):
        raise ValueError("范围与语法检查尚未通过，不能建立验证计划")
    from app.workflow.acceptance import validate_acceptance

    contract = validate_acceptance(state)
    reproduction = state.get("baseline_reproduction") or {}
    if (
        reproduction.get("status") != "passed"
        or reproduction.get("problem_id") != contract.problem_id
        or reproduction.get("baseline_id") != contract.baseline_id
    ):
        raise ValueError("BASELINE_NOT_REPRODUCED: 修改前基线未复现原始异常")
    functions = changed_functions(
        Path(state["ai_workspace_path"]), Path(state["baseline_dir"]), files
    )
    plan = {
        "workflow_version": WORKFLOW_VERSION,
        "evidence_version": version,
        "gate_policy_id": hashlib.sha256(
            Path(verification.__file__).read_bytes()
        ).hexdigest(),
        "changed_files": files,
        "changed_symbols": functions,
        "input_id": fingerprint(problem),
        "has_original_response": bool(body),
        "original_error": str(problem.get("error") or problem.get("raw_error") or ""),
        "issue_required": True,
        "request_required": True,
        "sample_policy": "original_recording_and_actual_request",
        "acceptance": [
            "原始异常在基线复现且修复后符合框架协议",
            "原请求通过 server_dev.main 执行并核对实际结果",
        ],
        "review_mode": state.get("review_mode", "agent"),
    }
    plan["binding"] = EvidenceBinding(
        problem_id=contract.problem_id,
        baseline_id=contract.baseline_id,
        acceptance_id=contract.contract_id,
        revision_id=version["patch_id"],
        policy_id=plan["gate_policy_id"],
    ).model_dump(mode="json")
    plan["plan_id"] = fingerprint(plan)
    return ValidationPlan.model_validate(plan).model_dump(mode="json")


def summarize(state: dict[str, Any]) -> dict[str, Any]:
    from pathlib import Path

    from app.agents.review.tools.check_changes import verification

    plan = state.get("validation_plan") or {}
    if plan.get("workflow_version") != WORKFLOW_VERSION:
        raise ValueError("验证计划版本无效，必须重新运行当前工作流")
    version = plan["evidence_version"]
    current = evidence_version(
        state["ai_workspace_path"],
        state.get("changed_files") or [],
        state["baseline_dir"],
    )
    stale = (
        plan.get("plan_id")
        != fingerprint({k: v for k, v in plan.items() if k != "plan_id"})
        or current != version
        or fingerprint(state.get("problem") or {}) != plan["input_id"]
        or not (state.get("gate_result") or {}).get("passed")
        or not (state.get("syntax_result") or {}).get("passed")
        or (state.get("gate_result") or {}).get("evidence_version", {}).get("patch_id")
        != version["patch_id"]
    )
    stale = (
        stale
        or plan["gate_policy_id"]
        != hashlib.sha256(Path(verification.__file__).read_bytes()).hexdigest()
        or (state.get("gate_result") or {}).get("evidence_version", {}).get("policy_id")
        != plan["gate_policy_id"]
    )
    dimensions = {}
    for name, key in (("issue", "verify_result"), ("request", "regression_result")):
        report = state.get(key) or {}
        if (
            report
            and (report.get("evidence_version") or {}).get("patch_id")
            != version["patch_id"]
        ):
            stale = True
        if (
            report.get("status") == "verified"
            and report.get("input_id") != plan["input_id"]
        ):
            stale = True
        if (
            name == "issue"
            and report.get("status") == "verified"
            and (report.get("evidence_version") or {}).get("baseline_id")
            != version["baseline_id"]
        ):
            stale = True
        if name == "issue":
            passed = (
                verification_is_complete(report)
                and plan["has_original_response"]
                and bool(plan["original_error"])
            )
            unavailable = (
                not plan["has_original_response"] or not plan["original_error"]
            )
        else:
            actual = report.get("request_regression") or {}
            passed = (
                report.get("passed") is True
                and report.get("status") == "verified"
                and actual.get("passed") is True
                and actual.get("status") == "verified"
                and actual.get("entrypoint") == "server_dev.main"
            )
            unavailable = (
                report.get("status")
                in {"not_configured", "not_executed", "syntax_only"}
                or report.get("failure_kind") == "policy"
                or (not actual and report.get("failure_kind") != "execution")
            )
        status = (
            "passed"
            if passed
            else "failed"
            if report.get("status") == "failed"
            else "unverified"
        )
        dimensions[name] = {
            "status": status,
            "reason": str(report.get("reason") or "未取得有效执行结果"),
            "retryable": status == "unverified" and not unavailable,
            "report_path": report.get("report_path", ""),
        }
    from app.workflow.acceptance import validate_acceptance

    try:
        contract = validate_acceptance(state)
        stale = stale or contract.contract_id != plan["binding"]["acceptance_id"]
    except ValueError:
        stale = True
    results = []
    issue_assertions = (
        (state.get("verify_result") or {}).get("verification") or {}
    ).get("assertions") or []
    for key in ("input", "baseline", "fixed", "request"):
        dimension = dimensions["request" if key == "request" else "issue"]
        observed = next(
            (item for item in issue_assertions if item.get("key") == key), {}
        )
        status = (
            dimension["status"]
            if key == "request"
            else observed.get("status", "unverified")
        )
        results.append(
            AssertionResult(
                assertion_id=key,
                status=status,
                reason_code=status,
                summary=dimension["reason"],
                report_path=dimension["report_path"],
                failure_origin="code"
                if status == "failed" and key != "request"
                else "unknown"
                if status in {"failed", "unverified"}
                else None,
            )
        )
    return ValidationSummary.model_validate(
        {
            "workflow_version": WORKFLOW_VERSION,
            "plan_id": plan["plan_id"],
            "evidence_version": version,
            "stale": stale,
            "dimensions": dimensions,
            "changed_symbols": plan["changed_symbols"],
            "report": ValidationReport(
                binding=EvidenceBinding.model_validate(plan["binding"]), results=results
            ),
        }
    ).model_dump(mode="json")


def business_recovery(run_regression: dict[str, Any] | None) -> dict[str, str]:
    """把真实请求回归收敛成审批面可直接展示的业务恢复状态。

    ``verified``：真实请求返回了有效业务结果；``failed``：请求已执行但框架
    记录了业务错误；``unverified``：请求未完成或无法确认业务结果；
    ``not_executed``：本轮没有执行真实请求。该字段只描述业务恢复，不替代
    代码审查结论。
    """
    output = run_regression if isinstance(run_regression, dict) else {}
    request = output.get("request_regression")
    if not isinstance(request, dict) or not request:
        return {"status": "not_executed", "reason": "本轮未执行真实请求"}
    status = str(request.get("status") or "").lower()
    reason = str(request.get("reason") or output.get("reason") or "")
    if status == "verified" and request.get("passed") is True:
        return {"status": "verified", "reason": reason or "真实请求返回有效业务结果"}
    if status == "failed":
        return {"status": "failed", "reason": reason or "真实请求返回业务错误"}
    return {"status": "unverified", "reason": reason or "真实请求未完成业务验证"}


def decide(state: dict[str, Any]) -> dict[str, Any]:
    summary = state.get("validation_summary") or {}
    if state.get("validation_plan"):
        summary = summarize(state)
    review = state.get("review") or {}
    attempts = dict(state.get("validation_recovery_counts") or {})
    repair_attempt = int(state.get("automatic_attempt") or 1)
    limit = max(1, int(state.get("max_attempts") or settings.default_max_attempts))
    target, reason, action = "", "", "blocked"
    human = state.get("review_mode") == "human"
    dimensions = summary.get("dimensions") or {}
    from app.agents.review.contract import judge
    checked_review = judge(review)
    current_code_failure = False
    if not human and review.get("code_verdict") == "FAIL" and checked_review.valid and not review.get("contract_error"):
        current_code_failure = review.get("evidence_version") == evidence_version(
            state["ai_workspace_path"], state.get("changed_files") or [], state["baseline_dir"]
        ) and review.get("review_contract_version") == REVIEW_CONTRACT_VERSION
    if current_code_failure:
        action, target, reason = "repair", "repair_agent", str(review.get("summary") or "代码审查确认修复存在缺陷")
    elif (
        (state.get("gate_result") or {}).get("passed") is False
        or (state.get("syntax_result") or {}).get("passed") is False
    ):
        # 确定性范围/语法检查已经判定补丁不合格：这是客观结论，不能被
        # "重新审查" 覆盖，否则会在 review_agent 与本节点之间空转直到
        # 恢复次数用尽，代码始终没有被修。
        action, target, reason = "repair", "repair_agent", "确定性检查未通过，回修代码"
    elif (
        summary.get("workflow_version") != WORKFLOW_VERSION
        or summary.get("stale")
        or set(dimensions) != {"issue", "request"}
    ):
        target, reason = (
            "review_agent",
            "源码或验证输入已变化，重新检查并建立验证计划",
        )
    elif not human:
        from app.agents.review.contract import judge

        checked = judge(review)
        current_review = (
            review.get("review_contract_version") == REVIEW_CONTRACT_VERSION
            and review.get("evidence_version") == summary.get("evidence_version")
            and not review.get("contract_error")
        )
        if not current_review or not checked.valid:
            target, reason = "review_agent", "需要取得当前补丁的三维代码审查结论"
        elif review.get("code_verdict") == "FAIL":
            action, target, reason = "repair", "repair_agent", str(review.get("summary") or "代码审查确认修复存在缺陷")
        elif checked.passed:
            action, reason = "deliver", "修复后代码审查通过，交付修复分支后进入人工复核"
        else:
            reason = str(review.get("summary") or "审查证据不足或存在待判断事项，不能确认修复成功")
    elif any(item.get("status") == "failed" for item in dimensions.values()):
        action, target, reason = (
            "repair",
            "repair_agent",
            "实际业务验证发现失败，回修代码",
        )
    else:
        pending = [
            (key, item)
            for key, item in dimensions.items()
            if item.get("status") != "passed"
        ]
        if pending:
            key, item = pending[0]
            target = (
                "review_agent"
                if item.get("retryable")
                else ""
            )
            reason = item["reason"]
        else:
            action, reason = "deliver", "验证完成，交付修复分支后进入人工复核"
    if target in RECOVERY_TARGETS:
        if attempts.get(target, 0) < limit - 1:
            action = "retry"
        else:
            action, target, reason = "blocked", "", f"自动恢复已达上限：{reason}"
    if action == "repair" and repair_attempt >= limit:
        action, target, reason = "blocked", "", f"代码回修已达上限：{reason}"
    return ValidationDecision.model_validate(
        {
            "workflow_version": WORKFLOW_VERSION,
            "plan_id": summary.get("plan_id"),
            "action": action,
            "target": target,
            "reason": reason,
            "manual_required": human and action == "deliver",
            "delivery_eligible": action == "deliver",
            "recovery_counts": attempts,
            "repair_attempt": repair_attempt,
        }
    ).model_dump(mode="json")


def assert_workflow_version(metadata: dict[str, Any]) -> None:
    if metadata.get("workflow_version") != WORKFLOW_VERSION:
        raise ValueError("旧版本工作流不支持继续执行，请新建任务；历史记录仅供查看")
