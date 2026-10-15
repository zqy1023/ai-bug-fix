"""10 人工审批。等待用 LangGraph interrupt。"""

from __future__ import annotations

from app.domain.errors import StepFailed
from app.application.context import StepContext
from app.workflow.steps.request_approval.approval import final_gate
from app.workflow.steps.request_approval.models import ApprovalInput, ApprovalOutput
from langgraph.types import interrupt


def run(ctx: StepContext, data: ApprovalInput) -> ApprovalOutput:
    """没有决定就挂起；审批内容绑定任务、文件清单与方案指纹。"""
    if data.decision == "PENDING":
        resumed = interrupt({
            "task_id": data.task_id,
            "approval_fingerprint": data.approval_fingerprint,
            "changed_files": list(data.changed_files),
            "authorized_files": list(data.authorized_files),
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

    if decision == "REJECT":
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
        raise StepFailed(reason, {"decision": "APPROVE", "passed": False, "reason": reason})
    return ApprovalOutput(decision="APPROVE", passed=True, reason="人工审批通过")


__all__ = ["run"]
