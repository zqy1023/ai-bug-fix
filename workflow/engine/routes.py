"""阶段流转条件分支判断与决策路由 (Routes & Decision Definitions)。

定义 Agent 工作流阶段、路由与拓扑依赖。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, TYPE_CHECKING

from langgraph.graph import END, START

from config.settings import settings
from core.contracts import WORKFLOW_VERSION, Dependency
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


def fingerprint(value: Any) -> str:
    """计算任意结构化数据的稳定 SHA-256 指纹。"""
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def confirmation(state: dict[str, Any]) -> tuple[bool, list[str]]:
    required = state.get("review_mode") == "human" or state.get("manual_confirmation_required") is True
    reasons = list(state.get("manual_confirmation_reasons") or [])
    return required, reasons or (["用户选择人工代码审查"] if required else [])


def assert_workflow_version(metadata: dict[str, Any]) -> None:
    if metadata.get("workflow_version") != WORKFLOW_VERSION:
        raise ValueError("旧版本工作流不支持继续执行，请新建任务；历史记录仅供查看")


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
    "CLAIM",
    "DEDUPLICATE",
    "DISCOVER",
    "FINALIZE",
    "IDENTIFY",
    "NORMALIZE",
    "PHASE_COMPOSITIONS",
    "PREPARE_CONTEXT",
    "PUBLIC_STAGE_TITLES",
    "RECOVERY_TARGETS",
    "REPORT",
    "RETRY",
    "RETRY_RESET_FAILED",
    "REVIEW",
    "RUN_PHASES",
    "RUN_PREPARE",
    "SOURCE_SYNC",
    "STEP_DEPENDENCIES",
    "SUBMIT",
    "TASK_CONDITIONAL_TRANSITIONS",
    "TASK_DISPATCH",
    "TASK_NODE_NAMES",
    "TASK_PHASES",
    "TASK_STATIC_TRANSITIONS",
    "UNIFIED_FIXER",
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
    "cancelled",
    "confirmation",
    "dependencies_for",
    "fingerprint",
    "next_step_for",
]
