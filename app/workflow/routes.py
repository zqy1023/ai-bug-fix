"""Deterministic routing decisions for the workflow graph."""

from __future__ import annotations

from app.workflow.state import WorkflowState
from app.domain.decisions import locate_disposition
from config.settings import settings
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
DELIVER = "prepare_delivery"
FINALIZE = "finalize_execution"
RETRY = "retry_prepare"


def after_run_prepare(state: WorkflowState) -> str:
    """日志发现、规范化和去重组合阶段完成后才识别运输商。"""
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return END
    return IDENTIFY if _result(state) == "passed" else END


def after_prepare_context(state: WorkflowState) -> str:
    """证据准备成功后必须先做根因分析；旧框架限制后续修改，不跳过定位。"""
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return FINALIZE
    return LOCATE if _result(state) == "passed" else FINALIZE


def after_validate_changes(state: WorkflowState) -> str:
    """变更门禁和问题验证均通过后才执行回归。

    ``syntax_only`` 只表示日志里没有可重放的 response，重放被跳过。
    语法检查已经通过，不能因此跳过回归、审查和交付。
    """
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return FINALIZE
    if _result(state) != "passed":
        return _retry_or_finalize(state)
    return REGRESSION


def after_identify(state: WorkflowState) -> str:
    """识别出任务后交给串行分发节点；识别失败则整轮结束。"""
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return END
    return TASK_DISPATCH if _result(state) == "passed" else END


def _result(state: WorkflowState) -> str:
    return str(state.get("last_result") or "failed")


def _delivery_status(state: WorkflowState) -> str:
    """任务汇总或交付回写里的状态，统一转大写比较。"""
    summary = str((state.get("task_summary") or {}).get("delivery_status") or "")
    delivery = str((state.get("delivery") or {}).get("delivery_status") or "")
    return (summary or delivery).upper()


def _finalized_status(state: WorkflowState) -> str:
    """只有 finalize_execution 写出的 task_summary 才表示任务已经收尾。"""
    return str((state.get("task_summary") or {}).get("delivery_status") or "").upper()


def _retry_or_finalize(state: WorkflowState) -> str:
    return FINALIZE if int(state.get("attempt") or 1) >= int(
        state.get("max_attempts") or settings.default_max_attempts
    ) else RETRY


def _locate_references_legacy(locate: dict) -> bool:
    """定位 Agent 若把根因证据指向 spiders，则强制走分析报告分支。"""
    paths = [str(item) for item in locate.get("candidate_files") or []]
    for item in locate.get("source_evidence") or []:
        if isinstance(item, dict):
            paths.append(str(item.get("path") or ""))
    return any(path.replace("\\", "/").startswith("spiders/") for path in paths)


def after_retry(state: WorkflowState) -> str:
    """按失败/驳回的明确目标重跑阶段；未知目标安全回退到代码修复。"""
    if str(state.get("terminal_status") or "").upper() == "CANCELLED":
        return FINALIZE
    target = str(state.get("retry_target") or "")
    if target == "__retry_reset_failed__":
        return FINALIZE
    if target in {LOCATE, REPAIR, VALIDATE, REGRESSION, REVIEW, DELIVER}:
        return target
    return REPAIR


def after_claim(state: WorkflowState) -> str:
    """定位通过且成功领取修复资格后，才进入修复 Agent。隔离区已在定位前创建。"""
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return FINALIZE
    return REPAIR if _result(state) == "passed" else FINALIZE


def after_locate(state: WorkflowState) -> str:
    """仅在证据支持的根因定位成功后，才领取本轮修复资格。"""
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return FINALIZE
    if _result(state) == "failed":
        return _retry_or_finalize(state)
    # 旧框架任务只做根因分析；即使 Locate 返回 LOCATED，也不能进入自动修复。
    evidence = state.get("evidence") or {}
    if evidence.get("legacy_only") or evidence.get("legacy_dependency"):
        return FINALIZE
    locate = state.get("locate") or {}
    if _locate_references_legacy(locate):
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
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return FINALIZE
    # 共享层范围升级即使带有 change_plan，也不能进入自动修改或审批。
    if state.get("scope_escalation_required"):
        # 共享层/范围升级不属于允许自动修改的 carrier 局部问题。
        return FINALIZE
    if _result(state) != "passed":
        return _retry_or_finalize(state)
    # VALIDATE 是图中的组合阶段节点；CHECK 是组合阶段内部步骤，不能作为直接路由目标。
    return VALIDATE


def after_regression(state: WorkflowState) -> str:
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return FINALIZE
    if _result(state) != "passed":
        return _retry_or_finalize(state)
    # human 模式跳过 AI 审查，但仍要产出修复分支：交付后由人工在审批节点审查分支。
    if str(state.get("review_mode") or settings.default_review_mode).lower() == "human":
        return DELIVER
    return REVIEW


def after_review(state: WorkflowState) -> str:
    """AI 审查后先完成分支交付；只有分支成功落地后才进入最终人工审批。

    PASS：交付分支后进入最终审批。
    MANUAL：AI 无法自动给出确定结论，但它不是“转分析报告”；仍交付候选修复分支，
    由最终人工审批检查真实 diff 并决定保留或拒绝。
    FAIL：在上限内回修，超过上限后由 finalize 记录失败。
    """
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return FINALIZE
    if _result(state) == "failed":
        return _retry_or_finalize(state)

    review = state.get("review") or {}
    result = str(review.get("verdict") or review.get("result") or "").upper()

    if result == "FAIL":
        return _retry_or_finalize(state)

    # MANUAL 不代表进入审批态。此时分支还未提交，必须先完成交付。
    if result in {"PASS", "MANUAL"}:
        return DELIVER

    # 缺失或未知审查结论属于工作流契约异常，不能擅自放行。
    return FINALIZE


def after_approval(state: WorkflowState) -> str:
    """审批后路由：最终审批只保留或拒绝已交付分支，不自动合并。"""
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return FINALIZE
    if state.get("approval_cleanup_failed") or state.get("approval_recovery_blocked"):
        return FINALIZE

    approval_result = _result(state)
    # Rejected approval must carry a known target all the way from API to route.
    # Never allow legacy/malformed checkpoints to fall through after_retry's generic repair fallback.
    if str(state.get("decision") or "").upper() == "REJECT" or approval_result == "rejected":
        target = str(state.get("retry_target") or "")
        allowed_targets = {LOCATE, REPAIR, VALIDATE, REGRESSION, REVIEW, DELIVER}
        if target not in allowed_targets:
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
    if str((state.get("delivery") or {}).get("delivery_status") or "").upper() == "FAILED":
        return False
    locate = state.get("locate") or {}
    if not locate:
        return False
    locate_status = str(locate.get("status") or "").upper()
    if locate_status not in {"LOCATED", "MANUAL", "INSUFFICIENT_EVIDENCE"}:
        return False
    evidence = state.get("evidence") or {}
    if (
        evidence.get("legacy_only")
        or evidence.get("legacy_dependency")
        or state.get("scope_escalation_required")
        or locate_status in {"MANUAL", "INSUFFICIENT_EVIDENCE"}
    ):
        return True
    return bool(
        str(locate.get("recommended_scope") or "") != "carrier"
        or str(locate.get("category") or "") != "carrier"
        or _locate_references_legacy(locate)
    )


def after_delivery(state: WorkflowState) -> str:
    """源码交付失败必须回到终态记录失败；分析报告交付失败也不得伪装为人工分析完成。"""
    if str(state.get("terminal_status") or "").upper() == "CANCELLED" or _result(state) == "cancelled":
        return FINALIZE
    if _finalized_status(state) == "SKIPPED":
        return END
    if _result(state) != "passed" or str((state.get("delivery") or {}).get("delivery_status") or "").upper() == "FAILED":
        return FINALIZE
    if _analysis_only_task(state):
        return END
    return APPROVE


def after_finalize(state: WorkflowState) -> str:
    """只为明确分流到分析报告的任务交付 final.json；失败任务不伪装成分析报告。"""
    if str(state.get("terminal_status") or "").upper() == "CANCELLED":
        return END
    if _delivery_status(state) == "SKIPPED":
        return END
    if str(state.get("decision") or "").upper() == "REJECT" or state.get("approval_cleanup_failed"):
        return END
    # finalize_execution 已汇总出的执行失败必须终止；不能因 review=MANUAL 等业务结论
    # 再次进入报告交付，从而把节点失败伪装成“分析任务已完成”。
    if _finalized_status(state) in {"FAILED", "FAIL", "CANCELLED", "REJECTED"}:
        return END
    if _result(state) in {"failed", "cancelled", "rejected"}:
        return END
    if str((state.get("delivery") or {}).get("delivery_status") or "").upper() == "FAILED":
        return END
    return DELIVER if _analysis_only_task(state) and state.get("ai_workspace_path") else END


__all__ = [
    "APPROVE", "CHECK", "CLAIM", "DEDUPLICATE", "DELIVER", "DISCOVER",
    "PREPARE_CONTEXT", "RUN_PREPARE", "VALIDATE",
    "EVIDENCE", "FINALIZE", "IDENTIFY", "LOCATE", "NORMALIZE", "REGRESSION", "REPAIR",
    "REVIEW", "RETRY", "SNAPSHOT", "TASK_DISPATCH", "VERIFY", "WORKSPACE",
    "after_approval", "after_delivery", "after_finalize", "after_retry", "after_claim", "after_prepare_context",
    "after_run_prepare", "after_validate_changes",
    "after_identify", "after_locate", "after_regression", "after_repair", "after_review",
]
