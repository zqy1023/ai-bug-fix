"""10 交付后人工复核。等待用 LangGraph interrupt；打回时撤回已交付分支。"""

from __future__ import annotations

from langgraph.types import interrupt

from core.contracts import StepContext
from core.errors import StepFailed
from infrastructure.storage.redaction import public_exception
from .approval import final_gate
from .models import (
    APPROVAL_RETRY_TARGETS,
    ApprovalInput,
    ApprovalOutput,
)


def run(ctx: StepContext, data: ApprovalInput) -> ApprovalOutput:
    """没有决定就挂起；打回须包含原因和白名单内的回修阶段，并先撤回交付分支。"""
    decision_reason = str(data.reason or "").strip()[:1000]
    retry_target = str(data.retry_target or "")
    if data.decision == "PENDING":
        resumed = interrupt(
            {
                "kind": "change_approval",
                "task_id": data.task_id,
                "approval_fingerprint": data.approval_fingerprint,
                "manual_confirmation_required": data.manual_confirmation_required,
                "manual_confirmation_reasons": list(data.manual_confirmation_reasons),
                "changed_files": list(data.changed_files),
                "actions": ["APPROVE", "REJECT"],
                "retry_targets": sorted(APPROVAL_RETRY_TARGETS),
            }
        )
        if isinstance(resumed, dict):
            decision = str(resumed.get("decision") or "PENDING").upper()
            supplied_fingerprint = str(resumed.get("approval_fingerprint") or "")
            if supplied_fingerprint != data.approval_fingerprint:
                raise StepFailed(
                    "审批指纹不匹配，拒绝使用过期审批",
                    {
                        "decision": "PENDING",
                        "passed": False,
                        "reason": "审批指纹不匹配",
                    },
                )
            decision_reason = str(resumed.get("reason") or "").strip()[:1000]
            retry_target = str(resumed.get("retry_target") or "")
        else:
            raise StepFailed(
                "审批恢复值必须包含决定和审批指纹",
                {
                    "decision": "PENDING",
                    "passed": False,
                    "reason": "审批恢复值格式无效",
                },
            )
    else:
        decision = data.decision

    if decision == "REJECT":
        if retry_target not in APPROVAL_RETRY_TARGETS:
            raise StepFailed(
                "驳回恢复节点无效，拒绝执行",
                {
                    "decision": "REJECT",
                    "passed": False,
                    "reason": "驳回恢复节点无效",
                    "retry_target": "",
                },
                result="manual",
            )

        if data.delivery_pushed or data.delivery_local_branch:
            if ctx.services.delivery is None:
                raise StepFailed(
                    "审批拒绝但交付服务未配置，无法删除已交付分支",
                    {
                        "decision": "REJECT",
                        "approval_cleanup_failed": True,
                        "passed": False,
                    },
                    result="manual",
                )
            try:
                ctx.services.delivery.discard_branch(
                    {
                        "run_id": ctx.run.run_id,
                        "task_id": data.task_id,
                        "source_repo_path": data.source_repo_path,
                        "repair_branch": data.repair_branch,
                        "pushed": data.delivery_pushed,
                        "local_branch": data.delivery_local_branch,
                    }
                )
            except Exception as error:
                reason = public_exception(error, prefix="审批拒绝后清理修复分支失败")
                raise StepFailed(
                    reason,
                    {
                        "decision": "REJECT",
                        "approval_cleanup_failed": True,
                        "passed": False,
                        "reason": reason,
                    },
                    result="manual",
                ) from error
        rejection_summary = decision_reason or "人工打回（未填写补充说明）"
        store = getattr(ctx, "store", None)
        if store is not None:
            receipt = (
                store.task_dir(ctx.run.run_id, data.task_id)
                / "delivery"
                / "operation.json"
            )
            if receipt.is_file():
                # 已确认删除旧分支后开始新的审批修订；旧收据留档但不能复用旧提交。
                import uuid

                receipt.replace(
                    receipt.with_name(f"operation.rejected-{uuid.uuid4().hex}.json")
                )
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
        raise StepFailed(
            "缺少人工决定",
            {"decision": "PENDING", "passed": False, "reason": "缺少人工决定"},
        )

    gate = final_gate(
        {
            "candidate_id": data.candidate_id,
            "evidence_version": data.evidence_version,
            "ai_workspace_path": data.ai_workspace_path,
            "carrier": data.carrier,
            "changed_files": list(data.changed_files),
            "baseline_manifest": data.baseline_manifest or None,
            "baseline_dir": data.baseline_dir or None,
            "agent_policy": data.agent_policy,
        }
    )
    if gate.get("passed") is not True:
        reason = str(gate.get("reason") or "最终门禁失败")
        if data.delivery_pushed or data.delivery_local_branch:
            try:
                ctx.services.delivery.discard_branch(
                    {
                        "run_id": ctx.run.run_id,
                        "task_id": data.task_id,
                        "source_repo_path": data.source_repo_path,
                        "repair_branch": data.repair_branch,
                        "pushed": data.delivery_pushed,
                        "local_branch": data.delivery_local_branch,
                    }
                )
            except Exception as error:
                cleanup_reason = public_exception(
                    error, prefix="最终门禁失败，清理修复分支也失败"
                )
                raise StepFailed(
                    cleanup_reason,
                    {
                        "decision": "REJECT",
                        "approval_cleanup_failed": True,
                        "passed": False,
                        "reason": reason,
                        "cleanup_error": cleanup_reason,
                    },
                    result="manual",
                ) from error
        raise StepFailed(
            reason,
            {"decision": "REJECT", "passed": False, "reason": reason},
            result="rejected",
        )
    return ApprovalOutput(
        candidate_id=data.candidate_id,
        decision="APPROVE",
        passed=True,
        reason="人工审批通过",
        retry_target="",
        manual_confirmation_required=data.manual_confirmation_required,
    )


__all__ = ["run"]

