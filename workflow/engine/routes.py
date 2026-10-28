"""阶段流转条件分支判断与决策路由 (Routes & Decision Definitions)。

整合工作流图路由、决策、验证门禁、验收契约与拓扑依赖定义。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal, TYPE_CHECKING

from langgraph.graph import END, START
from pydantic import Field

from agents.review.contract import REVIEW_CONTRACT_VERSION
from config.settings import settings
from core.contracts import (
    WORKFLOW_VERSION,
    AcceptanceAssertion,
    AcceptanceContract,
    AssertionResult,
    BaselineReproduction,
    Dependency,
    EvidenceBinding,
    FrozenModel,
    ValidationReport,
)
from core.policy import legacy_analysis_required

if TYPE_CHECKING:
    from workflow.engine.handoff import WorkflowState


# ---------------------------------------------------------------------------
# 1. 阶段节点常量与名称声明 (Phase Constants & Definitions)
# ---------------------------------------------------------------------------

DISCOVER = "discover_logs"
NORMALIZE = "normalize_error"
DEDUPLICATE = "deduplicate_error"
IDENTIFY = "identify_carrier"
TASK_DISPATCH = "task_dispatch"
RUN_PREPARE = "discover_and_normalize"
SOURCE_SYNC = "sync_source_repo"

# 权威 7 大业务阶段节点 + 终态收尾 + 重试准备
PREPARE_CONTEXT = "prepare_context"
CLAIM = "claim_repair_slot"
UNIFIED_FIXER = "unified_fixer_agent"
REVIEW = "review_agent"
REPORT = "generate_report"
SUBMIT = "submit_changes"
APPROVE = "request_approval"
FINALIZE = "finalize_execution"
RETRY = "retry_prepare"

RETRY_RESET_FAILED = "__retry_reset_failed__"

# 编排节点 -> 实际执行的原子插件
PHASE_COMPOSITIONS: dict[str, tuple[str, ...]] = {
    RUN_PREPARE: ("discover_logs", "normalize_error", "deduplicate_error"),
    PREPARE_CONTEXT: (
        "repair_branch_preflight",
        "prepare_source_snapshot",
        "prepare_workspace",
        "prepare_evidence",
    ),
}

PLAN = "plan_validation"
SUMMARY = "summarize_validation"
DECIDE = "decide_validation"

REVIEW_EVIDENCE_STEPS = (
    "check_changes",
    "syntax_regression",
    PLAN,
    "verify_issue",
    "run_regression",
    SUMMARY,
)

RUN_PHASES = (RUN_PREPARE,)
TASK_PHASES = {
    PREPARE_CONTEXT: PHASE_COMPOSITIONS[PREPARE_CONTEXT],
}

TASK_NODE_NAMES = (
    PREPARE_CONTEXT,
    CLAIM,
    UNIFIED_FIXER,
    REVIEW,
    REPORT,
    SUBMIT,
    APPROVE,
    FINALIZE,
    RETRY,
)

PUBLIC_STAGE_TITLES: dict[str, str] = {
    PREPARE_CONTEXT: "准备上下文",
    CLAIM: "资格领取确认",
    UNIFIED_FIXER: "统一分析修复模型",
    REVIEW: "修复后代码审查 Agent",
    REPORT: "汇总生成报告",
    SUBMIT: "提交候选分支",
    APPROVE: "人工审批",
    FINALIZE: "终态收尾",
    RETRY: "重试准备",
}

TASK_STATIC_TRANSITIONS: tuple[tuple[str, str], ...] = ((START, PREPARE_CONTEXT),)

RECOVERY_TARGETS = {
    "review_agent",
}


# ---------------------------------------------------------------------------
# 2. 验收契约模型与基线复现 (Acceptance Contracts)
# ---------------------------------------------------------------------------

def fingerprint(value: Any) -> str:
    """计算任意结构化数据的稳定 SHA-256 指纹。"""
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


class AcceptanceInput(FrozenModel):
    problem: dict[str, Any]
    baseline_dir: str
    baseline_manifest: dict[str, str]
    diagnosis: dict[str, Any]


def baseline_identity(directory: str, manifest: dict[str, str]) -> str:
    """核对完整基线文件集合，避免改动文件子集产生不同基线身份。"""
    root = Path(directory).resolve()
    if not root.is_dir() or not manifest:
        raise ValueError("BASELINE_MISSING: 缺少修复前源码基线")
    for relative, expected in manifest.items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"BASELINE_CHANGED: 基线文件不存在或越界: {relative}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"BASELINE_CHANGED: 基线文件内容变化: {relative}")
    return fingerprint(manifest)


def define_acceptance(data: AcceptanceInput) -> AcceptanceContract:
    """平台支持的结构化验收断言，在修改前固定。"""
    assertions = [
        AcceptanceAssertion(
            assertion_id="baseline",
            description="原始样本在基线复现原异常",
            method="baseline_replay",
            verifier="original_error",
            expected={"matches_original_error": True},
        ),
        AcceptanceAssertion(
            assertion_id="input",
            description="同一录制正文到达解析入口",
            method="fixed_replay",
            verifier="response_replay",
            expected={"input_delivered": True},
        ),
        AcceptanceAssertion(
            assertion_id="fixed",
            description="原异常被修复且返回符合框架业务协议",
            method="fixed_replay",
            verifier="response_replay",
            expected={"semantic_comparison_passed": True},
        ),
        AcceptanceAssertion(
            assertion_id="request",
            description="原请求通过框架入口并核对实际业务结果",
            method="live_request",
            verifier="server_dev.main",
            expected={"entrypoint": "server_dev.main", "business_passed": True},
        ),
    ]
    fields = {
        "problem_id": fingerprint(data.problem),
        "baseline_id": baseline_identity(data.baseline_dir, data.baseline_manifest),
        "diagnosis_id": fingerprint(data.diagnosis),
        "assertions": [item.model_dump(mode="json") for item in assertions],
    }
    return AcceptanceContract(contract_id=fingerprint(fields), **fields)


def validate_acceptance(state: dict[str, Any]) -> AcceptanceContract:
    """分别报告缺失、内容变化、问题变化和基线变化。"""
    raw = state.get("acceptance_contract")
    if not raw:
        raise ValueError("ACCEPTANCE_MISSING: 缺少修改前验收契约")
    contract = AcceptanceContract.model_validate(raw)
    fields = contract.model_dump(mode="json", exclude={"contract_id"})
    if fingerprint(fields) != contract.contract_id:
        raise ValueError("ACCEPTANCE_CHANGED: 验收契约内容身份不匹配")
    if fingerprint(state.get("problem") or {}) != contract.problem_id:
        raise ValueError("PROBLEM_CHANGED: 原始问题已变化")
    if (
        baseline_identity(state["baseline_dir"], state["baseline_manifest"])
        != contract.baseline_id
    ):
        raise ValueError("BASELINE_CHANGED: 验收契约与完整基线不匹配")
    if state.get("locate") and fingerprint(state["locate"]) != contract.diagnosis_id:
        raise ValueError("DIAGNOSIS_CHANGED: 定位结论已变化，需要重新定义验收")
    return contract


class ReproductionInput(FrozenModel):
    task_id: str
    carrier: str
    problem: dict[str, Any]
    baseline_dir: str
    baseline_manifest: dict[str, str]
    source_project_path: str
    ai_workspace_path: str
    acceptance_contract: AcceptanceContract
    candidates: list[str] = Field(default_factory=list)


def reproduce(ctx: Any, data: ReproductionInput) -> BaselineReproduction:
    """只运行未修改的代码，不把同一次执行猜作修复后结果。"""
    callback = ctx.services.metadata.get("independent_verify")
    if not callable(callback):
        raise ValueError("VERIFIER_MISSING: 基线重放验证器未配置")
    contract = validate_acceptance(data.model_dump(mode="json"))
    clean_source = (
        str(ctx.services.workspaces.project_root(ctx.services.source_repo))
        if ctx.services.workspaces and ctx.services.source_repo
        else ""
    )
    result = callback(
        {
            "task_id": data.task_id,
            "carrier": data.carrier,
            "normalized_log": data.problem,
            "baseline_dir": data.baseline_dir,
            "source_project_path": data.source_project_path,
            "clean_source_path": clean_source,
            "ai_workspace_path": data.ai_workspace_path,
            "changed_files": data.candidates or list(data.baseline_manifest),
            "baseline_only": True,
        }
    )
    path = "artifacts/baseline_reproduction.json"
    ctx.emit_artifact("baseline_reproduction", path, result)
    return BaselineReproduction(
        problem_id=contract.problem_id,
        baseline_id=contract.baseline_id,
        status="passed" if result.get("passed") is True else "unverified",
        retryable=result.get("retryable") is True
        or result.get("replay_verdict") == "environment",
        reason=str(result.get("reason") or "基线复现未完成"),
        report_path=path,
    )


# ---------------------------------------------------------------------------
# 3. 验证计划、事实、摘要与决策模型 (Validation Models & Logic)
# ---------------------------------------------------------------------------

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
        "unified_fixer_agent",
        "review_agent",
    ]
    reason: str
    manual_required: bool
    delivery_eligible: bool
    recovery_counts: dict[str, int]
    repair_attempt: int


class ValidationContext(FrozenModel):
    ai_workspace_path: str
    baseline_dir: str
    baseline_manifest: dict[str, str]
    problem: dict[str, Any]
    acceptance_contract: dict[str, Any]
    baseline_reproduction: dict[str, Any]
    changed_files: list[str] = Field(default_factory=list)
    syntax_result: dict[str, Any] = Field(default_factory=dict)
    gate_result: dict[str, Any] = Field(default_factory=dict)
    validation_plan: dict[str, Any] = Field(default_factory=dict)
    validation_summary: dict[str, Any] = Field(default_factory=dict)
    verify_result: dict[str, Any] = Field(default_factory=dict)
    regression_result: dict[str, Any] = Field(default_factory=dict)
    review: dict[str, Any] = Field(default_factory=dict)
    validation_recovery_counts: dict[str, int] = Field(default_factory=dict)
    automatic_attempt: int = 1
    max_attempts: int = 3
    review_mode: str = "agent"


def input_of(state: dict[str, Any]) -> dict[str, Any]:
    return {key: state[key] for key in ValidationContext.model_fields if key in state}


def confirmation(state: dict[str, Any]) -> tuple[bool, list[str]]:
    decision = state.get("validation_decision") or {}
    required = decision.get("manual_required") is True
    return required, ["用户选择人工代码审查"] if required else []


def assert_workflow_version(metadata: dict[str, Any]) -> None:
    if metadata.get("workflow_version") != WORKFLOW_VERSION:
        raise ValueError("旧版本工作流不支持继续执行，请新建任务；历史记录仅供查看")


def business_recovery(run_regression: dict[str, Any] | None) -> dict[str, Any]:
    """从全链路回归产物提取业务恢复状态供交付报告使用。"""
    if not isinstance(run_regression, dict):
        return {"status": "unverified"}
    req = run_regression.get("request_regression")
    if isinstance(req, dict) and req.get("status"):
        return {"status": req.get("status"), **req}
    if run_regression.get("passed"):
        return {"status": "verified", **run_regression}
    return {"status": run_regression.get("status") or "unverified", **run_regression}


def make_plan(state: dict[str, Any]) -> dict[str, Any]:
    from agents.review.gates.check_changes import verification
    from agents.review.gates.verify_issue.assessment import changed_functions
    from workflow.engine.handoff import evidence_version

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
    from agents.review.gates.check_changes import verification
    from agents.review.gates.verify_issue.assessment import verification_is_complete
    from workflow.engine.handoff import evidence_version

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


def decide(state: dict[str, Any]) -> dict[str, Any]:
    from workflow.engine.handoff import evidence_version

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
    from agents.review.contract import judge
    checked_review = judge(review)
    current_code_failure = False
    if not human and review.get("code_verdict") == "FAIL" and checked_review.valid and not review.get("contract_error"):
        current_code_failure = review.get("evidence_version") == evidence_version(
            state["ai_workspace_path"], state.get("changed_files") or [], state["baseline_dir"]
        ) and review.get("review_contract_version") == REVIEW_CONTRACT_VERSION
    if current_code_failure:
        action, target, reason = "repair", "unified_fixer_agent", str(review.get("summary") or "代码审查确认修复存在缺陷")
    elif (
        (state.get("gate_result") or {}).get("passed") is False
        or (state.get("syntax_result") or {}).get("passed") is False
    ):
        action, target, reason = "repair", "unified_fixer_agent", "确定性检查未通过，回修代码"
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
        checked = judge(review)
        current_review = (
            review.get("review_contract_version") == REVIEW_CONTRACT_VERSION
            and review.get("evidence_version") == summary.get("evidence_version")
            and not review.get("contract_error")
        )
        if not current_review or not checked.valid:
            target, reason = "review_agent", "需要取得当前补丁的三维代码审查结论"
        elif review.get("code_verdict") == "FAIL":
            action, target, reason = "repair", "unified_fixer_agent", str(review.get("summary") or "代码审查确认修复存在缺陷")
        elif checked.passed:
            action, reason = "deliver", "修复后代码审查通过，准备本地候选并等待人工审批"
        else:
            reason = str(review.get("summary") or "审查证据不足或存在待判断事项，不能确认修复成功")
    elif any(item.get("status") == "failed" for item in dimensions.values()):
        action, target, reason = (
            "repair",
            "unified_fixer_agent",
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
            action, reason = "deliver", "验证完成，准备本地候选并等待人工审批"
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


# ---------------------------------------------------------------------------
# 4. 依赖分析与下一步推演 (Topology Dependencies & Progression)
# ---------------------------------------------------------------------------

STEP_DEPENDENCIES = {
    "claim_repair_slot": [
        ("prepare_source_snapshot", "task"),
        ("prepare_evidence", "task"),
        ("prepare_workspace", "task"),
    ],
    "unified_fixer_agent": [("claim_repair_slot", "task")],
    "review_agent": [("unified_fixer_agent", "task")],
    "generate_report": [("unified_fixer_agent", "task")],
    "submit_changes": [("generate_report", "task")],
    "request_approval": [("submit_changes", "task")],
    "finalize_execution": [],
    "deduplicate_error": [("normalize_error", "run")],
    "discover_logs": [],
    "identify_carrier": [("deduplicate_error", "run")],
    "normalize_error": [("discover_logs", "run")],
    "prepare_evidence": [
        ("identify_carrier", "run"),
        ("prepare_source_snapshot", "task"),
        ("prepare_workspace", "task"),
    ],
    "prepare_source_snapshot": [("repair_branch_preflight", "task")],
    "prepare_workspace": [
        ("identify_carrier", "run"),
        ("prepare_source_snapshot", "task"),
    ],
    "repair_branch_preflight": [("identify_carrier", "run")],
}


def dependencies_for(name: str, review_mode: str = "agent") -> tuple[Dependency, ...]:
    """完整执行、单步执行和恢复操作共用的当前步骤依赖。"""
    return tuple(
        Dependency(step, level) for step, level in STEP_DEPENDENCIES.get(name, ())
    )


def cancelled(state: dict[str, Any]) -> bool:
    return str(state.get("terminal_status") or "").upper() == "CANCELLED"


def next_step_for(name: str, state: dict[str, Any]) -> str | None:
    """从权威图计算下一步；组合步骤只在组合内部顺序推进。"""
    for phase, members in PHASE_COMPOSITIONS.items():
        if name in members:
            index = members.index(name)
            if index + 1 < len(members):
                return members[index + 1]
            name = phase
            break
    for source, router, targets in TASK_CONDITIONAL_TRANSITIONS:
        if source == name:
            return router(state)
    return {
        "sync_source_repo": RUN_PREPARE,
        RUN_PREPARE: "identify_carrier",
        "identify_carrier": "task_dispatch",
    }.get(name)


# ---------------------------------------------------------------------------
# 5. 条件分支路由器 (Conditional Routers)
# ---------------------------------------------------------------------------

def _result(state: dict[str, Any]) -> str:
    return str(state.get("last_result") or "failed")


def _retry_or_finalize(state: dict[str, Any]) -> str:
    """代码回修与阶段执行恢复分别计数，所有恢复路径都有上限。"""
    limit = int(state.get("max_attempts") or settings.default_max_attempts)
    target = str(state.get("retry_target") or "")
    if target == UNIFIED_FIXER:
        exhausted = (
            int(state.get("automatic_attempt") or state.get("attempt") or 1) >= limit
        )
    else:
        exhausted = (
            int((state.get("validation_recovery_counts") or {}).get(target, 0))
            >= limit - 1
        )
    return FINALIZE if exhausted else RETRY


def after_source_sync(state: dict[str, Any]) -> str:
    """源码同步收到用户取消时立即结束，不再进入日志发现与任务执行。"""
    if (
        str(state.get("terminal_status") or "").upper() == "CANCELLED"
        or _result(state) == "cancelled"
    ):
        return END
    return RUN_PREPARE


def after_run_prepare(state: dict[str, Any]) -> str:
    """日志发现、规范化和去重组合阶段完成后才识别运输商。"""
    if (
        str(state.get("terminal_status") or "").upper() == "CANCELLED"
        or _result(state) == "cancelled"
    ):
        return END
    return IDENTIFY if _result(state) == "passed" else END


def after_prepare_context(state: dict[str, Any]) -> str:
    """准备上下文完成后，优先领取修复资格以确保分支互斥并防止白白浪费大模型推理资源。"""
    if (
        str(state.get("terminal_status") or "").upper() == "CANCELLED"
        or _result(state) == "cancelled"
    ):
        return FINALIZE
    if _result(state) == "passed":
        return CLAIM
    if (
        _result(state) == "failed"
        and str(state.get("retry_target") or "") == PREPARE_CONTEXT
    ):
        return _retry_or_finalize(state)
    return FINALIZE


def after_claim(state: dict[str, Any]) -> str:
    """成功领取修复资格后进入统一分析修复模型；槽位冲突或未获取资格直接收尾转人工。"""
    if (
        str(state.get("terminal_status") or "").upper() == "CANCELLED"
        or _result(state) == "cancelled"
    ):
        return FINALIZE
    if _result(state) == "passed":
        return UNIFIED_FIXER
    if _result(state) == "failed" and str(state.get("retry_target") or "") == CLAIM:
        return _retry_or_finalize(state)
    return FINALIZE


def after_unified_fixer(state: dict[str, Any]) -> str:
    """统一分析修复模型路由：修复完成进入审查模型，仅分析或转人工直接进入汇总生成报告。"""
    if (
        str(state.get("terminal_status") or "").upper() == "CANCELLED"
        or _result(state) == "cancelled"
    ):
        return FINALIZE
    if _result(state) == "failed" and not state.get("analysis_only"):
        target = str(state.get("retry_target") or "")
        if target == UNIFIED_FIXER:
            return _retry_or_finalize(state)

    if (
        state.get("analysis_only")
        or legacy_analysis_required(state)
        or str(state.get("terminal_status") or "").upper() == "MANUAL"
        or not bool(state.get("changed_files"))
    ):
        return REPORT

    if _result(state) == "passed":
        return REVIEW
    return REPORT


def after_review(state: dict[str, Any]) -> str:
    """审查后路由：审查失败且开启自动回修时打回统一模型，否则统一进入汇总生成报告。"""
    if (
        str(state.get("terminal_status") or "").upper() == "CANCELLED"
        or _result(state) == "cancelled"
    ):
        return FINALIZE
    from core.policy import policy_of

    policy = policy_of(state)
    review = state.get("review") or {}
    code_verdict = str(review.get("code_verdict") or "").upper()
    verdict = str(review.get("verdict") or "").upper()
    needs_repair = code_verdict == "FAIL" or verdict == "FAIL"
    if needs_repair and policy.automatic_repair:
        limit = int(state.get("max_attempts") or settings.default_max_attempts)
        attempt = int(state.get("automatic_attempt") or state.get("attempt") or 1)
        if attempt < limit:
            state["retry_target"] = UNIFIED_FIXER
            return RETRY
        return FINALIZE
    return REPORT


def after_report(state: dict[str, Any]) -> str:
    """汇总生成报告与交付文件后，提交候选分支。"""
    if (
        str(state.get("terminal_status") or "").upper() == "CANCELLED"
        or _result(state) == "cancelled"
    ):
        return FINALIZE
    if _result(state) == "failed":
        return _retry_or_finalize(state)
    return SUBMIT


def after_submit(state: dict[str, Any]) -> str:
    """提交并推送分支后，进入人工审批。"""
    if (
        str(state.get("terminal_status") or "").upper() == "CANCELLED"
        or _result(state) == "cancelled"
    ):
        return FINALIZE
    if _result(state) == "failed":
        return _retry_or_finalize(state)
    return APPROVE


def after_finalize(state: dict[str, Any]) -> str:
    """终态收尾完成后直接结束。"""
    return END


def _analysis_only_task(state: dict[str, Any]) -> bool:
    """仅按明确的业务分流原因判断报告交付，不用 FAILED/MANUAL 终态反推原因。"""
    if str((state.get("delivery") or {}).get("delivery_status") or "").upper() == "FAILED":
        return False
    if state.get("analysis_only"):
        return True
    evidence = state.get("evidence") or {}
    locate = state.get("locate") or {}
    review = state.get("review") or {}
    if (
        evidence.get("legacy_only")
        or evidence.get("legacy_dependency")
        or state.get("scope_escalation_required")
        or str(review.get("verdict") or "").upper() == "MANUAL"
    ):
        return True
    if not locate:
        return False
    return bool(
        str(locate.get("status") or "").upper() in {"MANUAL", "INSUFFICIENT_EVIDENCE"}
        or str(locate.get("recommended_scope") or "") != "carrier"
        or str(locate.get("category") or "") != "carrier"
        or legacy_analysis_required(state)
    )


def after_approval(state: dict[str, Any]) -> str:
    """人工审批：通过后收尾；驳回打回统一分析修复模型。"""
    if (
        str(state.get("terminal_status") or "").upper() == "CANCELLED"
        or _result(state) == "cancelled"
    ):
        return FINALIZE
    if state.get("approval_cleanup_failed") or state.get("approval_recovery_blocked"):
        return FINALIZE

    approval_result = _result(state)
    if (
        str(state.get("decision") or "").upper() == "REJECT"
        or approval_result == "rejected"
    ):
        state["retry_target"] = UNIFIED_FIXER
        return _retry_or_finalize(state)

    return FINALIZE


def after_identify(state: dict[str, Any]) -> str:
    """识别出任务后交给串行分发节点；识别失败则整轮结束。"""
    if (
        str(state.get("terminal_status") or "").upper() == "CANCELLED"
        or _result(state) == "cancelled"
    ):
        return END
    return TASK_DISPATCH if _result(state) == "passed" else END


def after_retry(state: dict[str, Any]) -> str:
    """重试恢复路由，按目标恢复对应节点执行。"""
    if str(state.get("terminal_status") or "").upper() == "CANCELLED":
        return FINALIZE
    target = str(state.get("retry_target") or "")
    if target == RETRY_RESET_FAILED:
        return FINALIZE
    if target in {
        PREPARE_CONTEXT,
        CLAIM,
        UNIFIED_FIXER,
        REVIEW,
        REPORT,
        SUBMIT,
        APPROVE,
        FINALIZE,
    }:
        return target
    return FINALIZE


TASK_CONDITIONAL_TRANSITIONS = (
    (PREPARE_CONTEXT, after_prepare_context, (CLAIM, RETRY, FINALIZE)),
    (CLAIM, after_claim, (UNIFIED_FIXER, RETRY, FINALIZE)),
    (UNIFIED_FIXER, after_unified_fixer, (REVIEW, REPORT, RETRY, FINALIZE)),
    (REVIEW, after_review, (REPORT, RETRY, FINALIZE)),
    (REPORT, after_report, (SUBMIT, RETRY, FINALIZE)),
    (SUBMIT, after_submit, (APPROVE, RETRY, FINALIZE)),
    (APPROVE, after_approval, (RETRY, FINALIZE)),
    (FINALIZE, after_finalize, (END,)),
    (
        RETRY,
        after_retry,
        (
            PREPARE_CONTEXT,
            CLAIM,
            UNIFIED_FIXER,
            REVIEW,
            REPORT,
            SUBMIT,
            APPROVE,
            FINALIZE,
        ),
    ),
)


__all__ = [
    "APPROVE",
    "AcceptanceInput",
    "CLAIM",
    "DEDUPLICATE",
    "DISCOVER",
    "FINALIZE",
    "IDENTIFY",
    "NORMALIZE",
    "PHASE_COMPOSITIONS",
    "PLAN",
    "PREPARE_CONTEXT",
    "PUBLIC_STAGE_TITLES",
    "RECOVERY_TARGETS",
    "REPORT",
    "RETRY",
    "RETRY_RESET_FAILED",
    "REVIEW",
    "REVIEW_EVIDENCE_STEPS",
    "RUN_PHASES",
    "RUN_PREPARE",
    "ReproductionInput",
    "SOURCE_SYNC",
    "STEP_DEPENDENCIES",
    "SUBMIT",
    "SUMMARY",
    "TASK_CONDITIONAL_TRANSITIONS",
    "TASK_DISPATCH",
    "TASK_NODE_NAMES",
    "TASK_PHASES",
    "TASK_STATIC_TRANSITIONS",
    "UNIFIED_FIXER",
    "ValidationContext",
    "ValidationDecision",
    "ValidationPlan",
    "ValidationSummary",
    "VerificationFact",
    "_analysis_only_task",
    "after_approval",
    "after_claim",
    "after_finalize",
    "after_identify",
    "after_prepare_context",
    "after_report",
    "after_retry",
    "after_review",
    "after_run_prepare",
    "after_source_sync",
    "after_submit",
    "after_unified_fixer",
    "assert_workflow_version",
    "baseline_identity",
    "business_recovery",
    "cancelled",
    "confirmation",
    "decide",
    "define_acceptance",
    "dependencies_for",
    "fingerprint",
    "input_of",
    "make_plan",
    "next_step_for",
    "reproduce",
    "summarize",
    "validate_acceptance",
]
