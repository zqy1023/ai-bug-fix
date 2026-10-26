"""Deterministic routing decisions for the workflow graph."""

from __future__ import annotations

from langgraph.graph import END

from app.domain.approval import APPROVAL_RETRY_TARGETS
from app.domain.decisions import locate_disposition
from app.domain.repair_scope import legacy_analysis_required
from app.workflow.state import WorkflowState
from config.settings import settings

DISCOVER = "discover_logs"
NORMALIZE = "normalize_error"
DEDUPLICATE = "deduplicate_error"
IDENTIFY = "identify_carrier"
# 父图里的串行分发节点。任务子图本身是它的下游，不在这里按名引用。
TASK_DISPATCH = "task_dispatch"
RUN_PREPARE = "discover_and_normalize"
SOURCE_SYNC = "sync_source_repo"
PREPARE_CONTEXT = "prepare_context"
CLAIM = "claim_repair_slot"
LOCATE = "locate_agent"
REPAIR = "repair_agent"
REVIEW = "review_agent"
APPROVE = "request_approval"
DELIVER = "prepare_delivery"
FINALIZE = "finalize_execution"
RETRY = "retry_prepare"
# 任务子图中的其余编排节点；此前以字面量散落各路由与图定义，统一在此声明。
DEFINE_ACCEPTANCE = "define_acceptance"
REPRODUCE = "reproduce_baseline"
PUBLISH = "publish_delivery"
ANALYSIS = "prepare_analysis"
# retry_prepare 归档失败时的哨兵目标：只允许进入 FINALIZE，禁止重入任何阶段。
RETRY_RESET_FAILED = "__retry_reset_failed__"


def after_source_sync(state: WorkflowState) -> str:
    """源码同步收到用户取消时立即结束，不再进入日志发现与任务执行。"""
    if (
        str(state.get("terminal_status") or "").upper() == "CANCELLED"
        or _result(state) == "cancelled"
    ):
        return END
    return RUN_PREPARE


def after_run_prepare(state: WorkflowState) -> str:
    """日志发现、规范化和去重组合阶段完成后才识别运输商。"""
    if (
        str(state.get("terminal_status") or "").upper() == "CANCELLED"
        or _result(state) == "cancelled"
    ):
        return END
    return IDENTIFY if _result(state) == "passed" else END


def after_prepare_context(state: WorkflowState) -> str:
    """准备阶段失败时在任务尝试上限内重跑整个准备阶段，不能直接杀死本轮后续任务。"""
    if (
        str(state.get("terminal_status") or "").upper() == "CANCELLED"
        or _result(state) == "cancelled"
    ):
        return FINALIZE
    if _result(state) == "passed":
        return LOCATE
    if (
        _result(state) == "failed"
        and str(state.get("retry_target") or "") == PREPARE_CONTEXT
    ):
        return _retry_or_finalize(state)
    return FINALIZE


def after_identify(state: WorkflowState) -> str:
    """识别出任务后交给串行分发节点；识别失败则整轮结束。"""
    if (
        str(state.get("terminal_status") or "").upper() == "CANCELLED"
        or _result(state) == "cancelled"
    ):
        return END
    return TASK_DISPATCH if _result(state) == "passed" else END


def _result(state: WorkflowState) -> str:
    return str(state.get("last_result") or "failed")


def _retry_or_finalize(state: WorkflowState) -> str:
    """代码回修与阶段执行恢复分别计数，所有恢复路径都有上限。"""
    limit = int(state.get("max_attempts") or settings.default_max_attempts)
    target = str(state.get("retry_target") or "")
    if target in {REPAIR, LOCATE}:
        exhausted = (
            int(state.get("automatic_attempt") or state.get("attempt") or 1) >= limit
        )
    else:
        exhausted = (
            int((state.get("validation_recovery_counts") or {}).get(target, 0))
            >= limit - 1
        )
    return FINALIZE if exhausted else RETRY


def after_retry(state: WorkflowState) -> str:
    """范围与语法通过后绑定补丁验证计划，业务结果由后续决策判定。"""
    if str(state.get("terminal_status") or "").upper() == "CANCELLED":
        return FINALIZE
    target = str(state.get("retry_target") or "")
    if target == RETRY_RESET_FAILED:
        return FINALIZE
    if target in {
        PREPARE_CONTEXT,
        CLAIM,
        LOCATE,
        REPAIR,
        REVIEW,
        DELIVER,
        PUBLISH,
        REPRODUCE,
    }:
        return target
    return FINALIZE


def after_claim(state: WorkflowState) -> str:
    """定位通过且成功领取修复资格后，才进入修复 Agent。"""
    if (
        str(state.get("terminal_status") or "").upper() == "CANCELLED"
        or _result(state) == "cancelled"
    ):
        return FINALIZE
    if _result(state) == "passed":
        return REPAIR
    if _result(state) == "failed" and str(state.get("retry_target") or "") == CLAIM:
        return _retry_or_finalize(state)
    return FINALIZE


def after_locate(state: WorkflowState) -> str:
    """仅在证据支持的根因定位成功后，才领取本轮修复资格。"""
    if (
        str(state.get("terminal_status") or "").upper() == "CANCELLED"
        or _result(state) == "cancelled"
    ):
        return FINALIZE
    if _result(state) == "failed":
        return _retry_or_finalize(state)
    locate = state.get("locate") or {}
    if legacy_analysis_required(state):
        return FINALIZE
    if (
        str(locate.get("recommended_scope") or "") != "carrier"
        or str(locate.get("category") or "") != "carrier"
    ):
        return FINALIZE
    disposition = locate_disposition(str(locate.get("status") or ""))
    if disposition == "repair" and _result(state) == "passed":
        # 重新定位发生在已领取修复资格之后时，直接回到 repair，避免重复 claim 导致资格冲突。
        return REPAIR if state.get("repair_slot_claimed") else CLAIM
    # 定位不确定或需要人工判断时只生成分析报告，不在中间节点挂起审批。
    return FINALIZE


def after_repair(state: WorkflowState) -> str:
    """修复后路由：共享层或范围升级只留分析报告，局部修复才进入验证链。"""
    if (
        str(state.get("terminal_status") or "").upper() == "CANCELLED"
        or _result(state) == "cancelled"
    ):
        return FINALIZE
    if str(state.get("terminal_status") or "").upper() == "MANUAL":
        return FINALIZE
    # 共享层范围升级即使带有 change_plan，也不能进入自动修改或审批。
    if state.get("scope_escalation_required") or legacy_analysis_required(state):
        # 共享层/范围升级不属于允许自动修改的 carrier 局部问题。
        return FINALIZE
    if _result(state) != "passed":
        return _retry_or_finalize(state)
    return REVIEW


def after_review(state: WorkflowState) -> str:
    from app.workflow.validation import DECIDE

    return (
        FINALIZE
        if str(state.get("terminal_status") or "").upper() == "CANCELLED"
        else DECIDE
    )


def after_approval(state: WorkflowState) -> str:
    """审批后路由：最终审批决定是否正式交付当前候选，不自动合并。"""
    if (
        str(state.get("terminal_status") or "").upper() == "CANCELLED"
        or _result(state) == "cancelled"
    ):
        return FINALIZE
    if state.get("approval_cleanup_failed") or state.get("approval_recovery_blocked"):
        return FINALIZE

    approval_result = _result(state)
    # Rejected approval must carry a known target all the way from API to route.
    # Never allow legacy/malformed checkpoints to fall through after_retry's generic repair fallback.
    if (
        str(state.get("decision") or "").upper() == "REJECT"
        or approval_result == "rejected"
    ):
        target = str(state.get("retry_target") or "")
        if target not in APPROVAL_RETRY_TARGETS:
            return FINALIZE
        return _retry_or_finalize(state)

    # 其他非通过状态：终止
    if approval_result != "passed":
        return FINALIZE

    # 最终审批只决定是否保留已交付分支供人工合并；工作流不自动合并。
    return FINALIZE


def _analysis_only_task(state: WorkflowState) -> bool:
    """仅按明确的业务分流原因判断报告交付，不用 FAILED/MANUAL 终态反推原因。

    交付失败或定位 Agent 执行失败都是执行失败，绝不能因此重新归类为分析报告任务。
    """
    if (
        str((state.get("delivery") or {}).get("delivery_status") or "").upper()
        == "FAILED"
    ):
        return False
    locate = state.get("locate") or {}
    if not locate:
        return False
    locate_status = str(locate.get("status") or "").upper()
    if locate_status not in {"LOCATED", "MANUAL", "INSUFFICIENT_EVIDENCE"}:
        return False
    if legacy_analysis_required(state):
        return True
    if (
        state.get("scope_escalation_required")
        or str(
            ((state.get("repair") or {}).get("change_plan") or {}).get("scope") or ""
        )
        == "manual"
        or locate_status in {"MANUAL", "INSUFFICIENT_EVIDENCE"}
    ):
        return True
    return bool(
        str(locate.get("recommended_scope") or "") != "carrier"
        or str(locate.get("category") or "") != "carrier"
    )


__all__ = [
    "APPROVE",
    "CLAIM",
    "DEDUPLICATE",
    "DELIVER",
    "DISCOVER",
    "PREPARE_CONTEXT",
    "RUN_PREPARE",
    "FINALIZE",
    "IDENTIFY",
    "LOCATE",
    "NORMALIZE",
    "REPAIR",
    "REVIEW",
    "RETRY",
    "TASK_DISPATCH",
    "after_approval",
    "after_retry",
    "after_claim",
    "after_prepare_context",
    "after_run_prepare",
    "after_identify",
    "after_locate",
    "after_repair",
    "after_review",
]
