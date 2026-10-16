"""10 人工审批。等待用 LangGraph interrupt。"""

from __future__ import annotations

from app.domain.errors import DeliveryError, StepFailed
from app.application.context import StepContext
from app.workflow.steps.request_approval.approval import final_gate
from app.workflow.steps.request_approval.models import ApprovalInput, ApprovalOutput
from langgraph.types import interrupt


def run(ctx: StepContext, data: ApprovalInput) -> ApprovalOutput:
    """没有决定就挂起；审批内容绑定任务、文件清单与方案指纹。"""
    if data.decision == "PENDING":
        resumed = interrupt({
            "kind": data.mode,
            "task_id": data.task_id,
            "approval_fingerprint": data.approval_fingerprint,
            "changed_files": list(data.changed_files),
            "authorized_files": list(data.authorized_files),
            "intervention_reason": data.intervention_reason,
            "candidate_files": list(data.candidate_files),
            "root_cause": data.root_cause,
            "recommended_scope": data.recommended_scope,
            "continue_allowed": data.continue_allowed,
            "actions": (["RETRY_LOCATE", "MARK_DONE", "TERMINATE"] + (["CONTINUE_REPAIR"] if data.continue_allowed else [])) if data.mode == "manual_intervention" else ["APPROVE", "REJECT"],
        })
        if isinstance(resumed, dict):
            decision = str(resumed.get("decision") or "PENDING").upper()
            supplied_fingerprint = str(resumed.get("approval_fingerprint") or "")
            if supplied_fingerprint != data.approval_fingerprint:
                raise StepFailed("审批指纹不匹配，拒绝使用过期审批", {"decision": "PENDING", "passed": False, "reason": "审批指纹不匹配"})
        else:
            decision = str(resumed or "PENDING").upper()
    else:
        decision = data.decision

    if data.mode == "manual_intervention":
        allowed = {"RETRY_LOCATE", "MARK_DONE", "TERMINATE"}
        if data.continue_allowed:
            allowed.add("CONTINUE_REPAIR")
        if decision not in allowed:
            raise StepFailed("人工介入决定无效或超出当前证据允许范围", {"decision": "PENDING", "passed": False, "reason": "人工介入决定无效或超出当前证据允许范围"})
        reasons = {
            "RETRY_LOCATE": "人工要求重新执行根因定位。",
            "CONTINUE_REPAIR": "人工批准继续进入修复流程；后续变更仍须通过范围门禁、验证和审查。",
            "MARK_DONE": "人工确认已在线下处理完毕，工作流不再自动修改代码。",
            "TERMINATE": "人工决定终止本任务的自动修复。",
        }
        return ApprovalOutput(decision=decision, passed=True, reason=reasons[decision])

    if decision == "REJECT":
        if data.delivery_pushed:
            if ctx.services.delivery is None:
                raise StepFailed(
                    "审批拒绝但交付服务未配置，无法删除已推送分支",
                    {"decision": "REJECT", "approval_cleanup_failed": True, "passed": False},
                    result="manual",
                )
            try:
                ctx.services.delivery.discard_branch({
                    "source_repo_path": data.source_repo_path,
                    "repair_branch": data.repair_branch,
                    "pushed": True,
                })
            except DeliveryError as error:
                raise StepFailed(
                    f"审批拒绝，但清理远程分支失败：{error}",
                    {"decision": "REJECT", "approval_cleanup_failed": True, "passed": False},
                    result="manual",
                ) from error
        raise StepFailed("人工打回", {"decision": "REJECT", "passed": False, "reason": "人工打回"}, result="rejected")
    if decision != "APPROVE":
        raise StepFailed("缺少人工决定", {"decision": "PENDING", "passed": False, "reason": "缺少人工决定"})

    gate = final_gate(
        {
            "ai_workspace_path": data.ai_workspace_path,
            "carrier": data.carrier,
            "changed_files": list(data.changed_files),
            "baseline_manifest": data.baseline_manifest or None,
            "baseline_dir": data.baseline_dir or None,
            "authorized_files": list(data.authorized_files),
        }
    )
    if gate.get("passed") is not True:
        reason = str(gate.get("reason") or "最终门禁失败")
        if data.delivery_pushed:
            try:
                ctx.services.delivery.discard_branch({
                    "source_repo_path": data.source_repo_path,
                    "repair_branch": data.repair_branch,
                    "pushed": True,
                })
            except (DeliveryError, AttributeError) as error:
                raise StepFailed(
                    f"最终门禁失败，且清理远程分支失败：{error}",
                    {"decision": "REJECT", "approval_cleanup_failed": True, "passed": False, "reason": reason},
                    result="manual",
                ) from error
        raise StepFailed(
            reason,
            {"decision": "REJECT", "passed": False, "reason": reason},
            result="rejected",
        )
    return ApprovalOutput(decision="APPROVE", passed=True, reason="人工审批通过")


__all__ = ["run"]
