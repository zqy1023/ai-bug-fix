"""10 人工审批。等待用 LangGraph interrupt。"""

from __future__ import annotations

from app.domain.errors import DeliveryError, StepFailed
from app.application.context import StepContext
from app.workflow.steps.request_approval.approval import final_gate
from app.workflow.steps.request_approval.models import ApprovalInput, ApprovalOutput
from langgraph.types import interrupt


_ALLOWED_RETRY_TARGETS = {
    "locate_agent", "repair_agent", "validate_changes",
    "run_regression", "review_agent", "prepare_delivery",
}


def run(ctx: StepContext, data: ApprovalInput) -> ApprovalOutput:
    """没有决定就挂起；驳回决定须包含原因和白名单内的恢复阶段。"""
    decision_reason = str(data.reason or "").strip()[:1000]
    retry_target = str(data.retry_target or "repair_agent")
    if data.decision == "PENDING":
        resumed = interrupt({
            "kind": "change_approval",
            "task_id": data.task_id,
            "approval_fingerprint": data.approval_fingerprint,
            "changed_files": list(data.changed_files),
            "actions": ["APPROVE", "REJECT"],
            "retry_targets": sorted(_ALLOWED_RETRY_TARGETS),
        })
        if isinstance(resumed, dict):
            decision = str(resumed.get("decision") or "PENDING").upper()
            supplied_fingerprint = str(resumed.get("approval_fingerprint") or "")
            if supplied_fingerprint != data.approval_fingerprint:
                raise StepFailed("审批指纹不匹配，拒绝使用过期审批", {"decision": "PENDING", "passed": False, "reason": "审批指纹不匹配"})
            decision_reason = str(resumed.get("reason") or "").strip()[:1000]
            retry_target = str(resumed.get("retry_target") or "repair_agent")
        else:
            raise StepFailed(
                "审批恢复值必须包含决定和审批指纹",
                {"decision": "PENDING", "passed": False, "reason": "审批恢复值格式无效"},
            )
    else:
        decision = data.decision

    if decision == "REJECT":
        if retry_target not in _ALLOWED_RETRY_TARGETS:
            raise StepFailed(
                "驳回恢复节点无效，拒绝执行",
                {"decision": "REJECT", "passed": False, "reason": "驳回恢复节点无效", "retry_target": ""},
                result="manual",
            )

        if data.delivery_pushed or data.delivery_local_branch:
            if ctx.services.delivery is None:
                raise StepFailed(
                    "审批拒绝但交付服务未配置，无法删除已交付分支",
                    {"decision": "REJECT", "approval_cleanup_failed": True, "passed": False},
                    result="manual",
                )
            try:
                ctx.services.delivery.discard_branch({
                    "source_repo_path": data.source_repo_path,
                    "repair_branch": data.repair_branch,
                    "pushed": data.delivery_pushed,
                    "local_branch": data.delivery_local_branch,
                })
            except DeliveryError as error:
                raise StepFailed(
                    f"审批拒绝，但清理修复分支失败：{error}",
                    {"decision": "REJECT", "approval_cleanup_failed": True, "passed": False},
                    result="manual",
                ) from error
        rejection_summary = decision_reason or "人工打回（未填写补充说明）"
        raise StepFailed(
            f"人工打回：{rejection_summary}",
            {
                "decision": "REJECT",
                "passed": False,
                "reason": rejection_summary,
                "retry_reason": rejection_summary,
                "retry_target": retry_target,
            },
            result="rejected",
        )
    if decision != "APPROVE":
        raise StepFailed("缺少人工决定", {"decision": "PENDING", "passed": False, "reason": "缺少人工决定"})

    gate = final_gate(
        {
            "ai_workspace_path": data.ai_workspace_path,
            "carrier": data.carrier,
            "changed_files": list(data.changed_files),
            "baseline_manifest": data.baseline_manifest or None,
            "baseline_dir": data.baseline_dir or None,
        }
    )
    if gate.get("passed") is not True:
        reason = str(gate.get("reason") or "最终门禁失败")
        if data.delivery_pushed or data.delivery_local_branch:
            try:
                ctx.services.delivery.discard_branch({
                    "source_repo_path": data.source_repo_path,
                    "repair_branch": data.repair_branch,
                    "pushed": data.delivery_pushed,
                    "local_branch": data.delivery_local_branch,
                })
            except (DeliveryError, AttributeError) as error:
                raise StepFailed(
                    f"最终门禁失败，且清理修复分支失败：{error}",
                    {"decision": "REJECT", "approval_cleanup_failed": True, "passed": False, "reason": reason},
                    result="manual",
                ) from error
        raise StepFailed(
            reason,
            {"decision": "REJECT", "passed": False, "reason": reason},
            result="rejected",
        )
    return ApprovalOutput(decision="APPROVE", passed=True, reason="人工审批通过", retry_target="")


__all__ = ["run"]
