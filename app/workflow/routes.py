"""Deterministic routing decisions for the workflow graph."""

from __future__ import annotations

from app.workflow.state import WorkflowState
from app.domain.decisions import locate_disposition
from langgraph.graph import END

DISCOVER = "discover_logs"
NORMALIZE = "normalize_error"
DEDUPLICATE = "deduplicate_error"
IDENTIFY = "identify_carrier"
# 父图里的串行分发节点。任务子图本身是它的下游，不在这里按名引用。
TASK_DISPATCH = "task_dispatch"
RUN_PREPARE = "discover_and_normalize"
PREPARE_CONTEXT = "prepare_context"
VALIDATE = "validate_changes"
CLAIM = "claim_repair_slot"
SNAPSHOT = "prepare_source_snapshot"
EVIDENCE = "prepare_evidence"
LOCATE = "locate_agent"
WORKSPACE = "prepare_workspace"
REPAIR = "repair_agent"
CHECK = "check_changes"
VERIFY = "verify_issue"
REGRESSION = "run_regression"
REVIEW = "review_agent"
APPROVE = "request_approval"
APPLY = "apply_plan"
DELIVER = "prepare_delivery"
FINALIZE = "finalize_execution"
RETRY = "retry_prepare"


def after_run_prepare(state: WorkflowState) -> str:
    """日志发现、规范化和去重组合阶段完成后才识别运输商。"""
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return END
    return IDENTIFY if _result(state) == "passed" else END


def after_prepare_context(state: WorkflowState) -> str:
    """快照与证据准备都成功且证据可用于自动定位时才进入 Locate Agent。"""
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return FINALIZE
    if _result(state) != "passed" or (state.get("evidence") or {}).get("legacy_only"):
        return FINALIZE
    return LOCATE


def after_validate_changes(state: WorkflowState) -> str:
    """变更门禁和问题验证均通过后才执行回归。"""
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return FINALIZE
    if _result(state) != "passed":
        return _retry_or_finalize(state)
    return REGRESSION


def after_deduplicate(state: WorkflowState) -> str:
    """去重后继续识别运输商；run 级步骤失败则整轮结束。"""
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return END
    return IDENTIFY if _result(state) == "passed" else END


def after_identify(state: WorkflowState) -> str:
    """识别出任务后交给串行分发节点；识别失败则整轮结束。"""
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return END
    return TASK_DISPATCH if _result(state) == "passed" else END


def _result(state: WorkflowState) -> str:
    return str(state.get("last_result") or "failed")


def _retry_or_finalize(state: WorkflowState) -> str:
    return FINALIZE if int(state.get("attempt") or 1) >= int(state.get("max_attempts") or 3) else RETRY


def _needs_human_authorization(state: WorkflowState) -> bool:
    if state.get("escalation_used"):
        return False
    plan = state.get("change_plan")
    return bool(state.get("scope_escalation_required")) and isinstance(plan, dict) and bool(plan)


def after_source_snapshot(state: WorkflowState) -> str:
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return FINALIZE
    return EVIDENCE if _result(state) == "passed" else FINALIZE


def after_evidence(state: WorkflowState) -> str:
    """证据准备成功后始终进入定位分析；旧框架只限制后续修改，不跳过根因分析。"""
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return FINALIZE
    if _result(state) != "passed":
        return FINALIZE
    return LOCATE


def after_claim(state: WorkflowState) -> str:
    """定位通过且成功领取本轮资格后，才创建隔离工作区。"""
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return FINALIZE
    return WORKSPACE if _result(state) == "passed" else FINALIZE


def after_locate(state: WorkflowState) -> str:
    """仅在证据支持的根因定位成功后，才领取本轮修复资格。"""
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return FINALIZE
    disposition = locate_disposition(str((state.get("locate") or {}).get("status") or ""))
    if disposition == "repair" and _result(state) == "passed":
        # 重新定位发生在已领取修复资格之后时，直接回到 repair，避免重复 claim 导致资格冲突。
        return REPAIR if state.get("repair_slot_claimed") else CLAIM
    # 对定位契约、证据或模型调用中断提供真正的人工中断点；不能直接 finalize 后只留下只读记录。
    if state.get("manual_intervention_required"):
        return APPROVE
    return FINALIZE


def after_workspace(state: WorkflowState) -> str:
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return FINALIZE
    return REPAIR if _result(state) == "passed" else FINALIZE


def after_repair(state: WorkflowState) -> str:
    """修复后路由：共享层方案先升级一次，否则通过的修复进入验证链。"""
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return FINALIZE
    # 失败也要先看升级：修复被共享层挡住时结果是 failed，但仍带 project 方案。
    # 共享层范围已升级但 Agent 没有产出可审批方案时，转入人工介入；不能悄悄重试或假装审批方案存在。
    if state.get("manual_intervention_required"):
        return APPROVE
    # escalation_used 保证同一任务只升级一次，用过之后回到重试或终止。
    if _needs_human_authorization(state):
        return APPROVE
    if _result(state) != "passed":
        return _retry_or_finalize(state)
    # VALIDATE 是图中的组合阶段节点；CHECK 是组合阶段内部步骤，不能作为直接路由目标。
    return VALIDATE


def after_check_changes(state: WorkflowState) -> str:
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return FINALIZE
    if _result(state) != "passed":
        return _retry_or_finalize(state)
    return VERIFY


def after_verify(state: WorkflowState) -> str:
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return FINALIZE
    if _result(state) != "passed":
        return _retry_or_finalize(state)
    return REGRESSION


def after_regression(state: WorkflowState) -> str:
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return FINALIZE
    if _result(state) != "passed":
        return _retry_or_finalize(state)
    return APPROVE if state.get("review_mode") == "human" else REVIEW


def after_review(state: WorkflowState) -> str:
    """审查后路由：PASS 后判断是否需要人工审批，FAIL 重试，MANUAL 转人工。

    审查子图回写的结论字段是 ``verdict``；``result`` 只在人工审查恢复时出现。
    """
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return FINALIZE
    review = state.get("review") or {}
    result = str(review.get("verdict") or review.get("result") or "").upper()

    # MANUAL：证据不足，停止自动处理并转人工线下排查。
    # 这里没有待审批方案，不能送进审批节点。
    if result == "MANUAL":
        return FINALIZE

    # FAIL：回到重试或终止
    if result == "FAIL" or _result(state) == "failed":
        return _retry_or_finalize(state)

    # PASS：判断是否需要人工审批
    if result == "PASS":
        if _needs_human_approval(state):
            return APPROVE
        return DELIVER

    # 其他情况终止
    return FINALIZE


def _needs_human_approval(state: WorkflowState) -> bool:
    """判断是否需要人工审批"""
    # 1. 共享层变更已在 repair 阶段处理过（escalation_used）
    if state.get("escalation_used"):
        return False

    # 2. 人工审查模式
    if state.get("review_mode") == "human":
        return True

    # 3. Review Agent 提出共享层方案
    review_plan = (state.get("review") or {}).get("change_plan")
    if isinstance(review_plan, dict) and str(review_plan.get("scope") or "") == "project" and bool(review_plan.get("edits")):
        return True

    # 4. 其他策略（敏感 carrier 等，可配置）
    # carrier = state.get("carrier") or ""
    # if carrier in settings.sensitive_carriers:
    #     return True

    return False


def after_approval(state: WorkflowState) -> str:
    """审批后路由：根据 scope 决定是 apply 共享层还是直接交付"""
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return FINALIZE
    # 定位阶段的人工处置与变更方案审批共用 interrupt，但决策语义完全分开。
    if str(state.get("approval_mode") or "") == "manual_intervention":
        decision = str(state.get("decision") or "").upper()
        if decision == "RETRY_LOCATE":
            return LOCATE
        if decision == "CONTINUE_REPAIR" and state.get("manual_continue_allowed"):
            # 定位阶段批准后首次领取修复资格；修复阶段缺少方案时直接重试 repair，不能二次 claim。
            return REPAIR if str(state.get("manual_resume_step") or "") == "repair_agent" or state.get("repair_slot_claimed") else CLAIM
        return FINALIZE

    approval_result = _result(state)
    # 人工明确拒绝后进入有上限的回修循环，不得继续 apply 或交付。
    if str(state.get("decision") or "").upper() == "REJECT" or approval_result == "rejected":
        return _retry_or_finalize(state)

    # 其他非通过状态：终止
    if approval_result != "passed":
        return FINALIZE

    # 批准后根据 scope 决定
    change_plan = state.get("change_plan") or {}
    scope = str(change_plan.get("scope") or "")

    if scope == "project":
        # 共享层方案需要 apply，然后重新验证
        return APPLY

    # carrier 修复直接交付（repair_agent 已在隔离工作区完成修改）
    return DELIVER


def after_apply(state: WorkflowState) -> str:
    """共享层落盘后必须重新验证，不能沿用落盘前的回归结论。"""
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return FINALIZE
    if _result(state) != "passed":
        return _retry_or_finalize(state)
    return VALIDATE


__all__ = [
    "APPLY", "APPROVE", "CHECK", "CLAIM", "DEDUPLICATE", "DELIVER", "DISCOVER",
    "PREPARE_CONTEXT", "RUN_PREPARE", "VALIDATE",
    "EVIDENCE", "FINALIZE", "IDENTIFY", "LOCATE", "NORMALIZE", "REGRESSION", "REPAIR",
    "REVIEW", "RETRY", "SNAPSHOT", "TASK_DISPATCH", "VERIFY", "WORKSPACE",
    "after_apply", "after_approval", "after_check_changes", "after_claim", "after_prepare_context",
    "after_run_prepare", "after_validate_changes",
    "after_deduplicate", "after_evidence", "after_identify", "after_locate",
    "after_regression", "after_repair", "after_review", "after_source_snapshot",
    "after_verify", "after_workspace",
]
