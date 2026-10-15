"""10 人工审批。等待用 LangGraph interrupt。"""

from __future__ import annotations


from app.domain.errors import StepFailed
from app.application.context import StepContext
from app.workflow.steps.request_approval.approval import final_gate
from app.workflow.steps.request_approval.models import ApprovalInput, ApprovalOutput
from langgraph.types import interrupt


def run(ctx: StepContext, data: ApprovalInput) -> ApprovalOutput:
    """没有决定就挂起。恢复值只接受 APPROVE 或 REJECT。"""
    decision = data.decision if data.decision != "PENDING" else str(interrupt({"task_id": data.task_id}) or "PENDING")
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
        }
    )
    if gate.get("passed") is not True:
        reason = str(gate.get("reason") or "最终门禁失败")
        raise StepFailed(reason, {"decision": "APPROVE", "passed": False, "reason": reason})
    return ApprovalOutput(decision="APPROVE", passed=True, reason="人工审批通过")


