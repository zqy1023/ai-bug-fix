"""11 创建修复分支并提交。不创建 MR，不自动合并。"""

from __future__ import annotations


from app.domain.errors import DeliveryError
from app.domain.errors import StepFailed
from app.application.context import StepContext
from app.workflow.steps.prepare_delivery.models import DeliveryInput, DeliveryOutput


def run(ctx: StepContext, data: DeliveryInput) -> DeliveryOutput:
    """把通过审查的修复交付到修复分支。交付前检查必要的门禁条件。"""

    if data.analysis_only:
        if not data.report_paths:
            raise StepFailed("分析任务交付失败：缺少最终报告", {"reason": "report_missing"})
    else:
        # 源码交付必须有实际改动，并且审查和必要审批均已通过。
        if not data.changed_files:
            raise StepFailed("交付失败：没有实际变更文件", {"changed_files": [], "reason": "no_changes"})
        review_decision = str(data.review_decision or "").upper()
        if review_decision not in ("PASS", "APPROVED"):
            raise StepFailed(
                f"交付失败：审查未通过，decision={review_decision}",
                {"review_decision": review_decision, "reason": "review_not_passed"},
                result="failed",
            )
        if data.requires_approval and not data.approval_granted:
            raise StepFailed(
                "交付失败：需要人工审批但未获批准",
                {"requires_approval": True, "approval_granted": False, "reason": "approval_required"},
                result="manual",
            )

    # 执行交付
    if ctx.services.delivery is None:
        raise StepFailed("交付服务未装配")

    try:
        result = ctx.services.delivery.deliver(
            {
                "task_id": data.task_id,
                "carrier": data.carrier,
                "repair_branch": data.repair_branch,
                "source_repo_path": data.source_repo_path,
                "ai_workspace_path": data.ai_workspace_path,
                "changed_files": list(data.changed_files),
                "authorized_files": list(data.authorized_files),
                "analysis_only": data.analysis_only,
                "report_paths": list(data.report_paths),
                "delivery_status": "ANALYSIS_ONLY" if data.analysis_only else "REPAIR",
                "error": data.error,
                "repair_analysis": data.repair_analysis,
                "repair_summary": data.repair_summary,
                "review_summary": data.review_summary,
            }
        )
    except DeliveryError as error:
        raise StepFailed(str(error)) from error

    return DeliveryOutput(
        repair_branch=str(result.get("repair_branch") or data.repair_branch),
        commit=str(result.get("commit") or ""),
        pushed=bool(result.get("pushed")),
    )


