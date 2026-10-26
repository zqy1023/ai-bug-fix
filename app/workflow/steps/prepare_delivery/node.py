"""创建修复分支并提交。不创建 MR，不自动合并。"""

from __future__ import annotations

import logging
import traceback
from pathlib import Path

from app.application.context import StepContext
from app.domain.errors import StepFailed
from app.infrastructure.redaction import public_exception
from app.workflow.steps.prepare_delivery.models import DeliveryInput, DeliveryOutput

logger = logging.getLogger(__name__)


def run(ctx: StepContext, data: DeliveryInput) -> DeliveryOutput:
    """把通过审查的修复交付到修复分支。交付前检查必要的门禁条件。"""
    from app.workflow.evidence_version import evidence_version
    from app.workflow.validation import fingerprint

    candidate_id = fingerprint(
        {
            "binding": data.binding.model_dump(mode="json") if data.binding else None,
            "branch": data.repair_branch,
            "files": sorted(data.changed_files),
        }
    )
    # 正式交付（publish）必须始终绑定到已审批的候选：analysis_only 只影响
    # "交付什么内容"，不能成为跳过候选身份校验的旁路。
    if data.publish or not data.analysis_only:
        if data.binding is None:
            raise StepFailed("交付缺少验证证据身份")
        current = evidence_version(data.ai_workspace_path, data.changed_files)
        if current["patch_id"] != data.binding.revision_id:
            raise StepFailed("补丁已变化，候选及审批已失效")
        if data.publish and data.approved_candidate_id != candidate_id:
            raise StepFailed("审批未绑定当前候选，禁止正式交付")
    if (
        data.review_decision.upper() in {"MANUAL", "HUMAN"}
        and not data.manual_confirmation_required
    ):
        data = data.model_copy(
            update={
                "manual_confirmation_required": True,
                "manual_confirmation_reasons": list(data.manual_confirmation_reasons)
                or [data.review_summary or "本次候选需要人工审查确认"],
            }
        )

    if data.analysis_only:
        if not data.report_paths:
            raise StepFailed(
                "分析任务交付失败：缺少最终报告", {"reason": "report_missing"}
            )
    else:
        from app.workflow.steps.prepare_delivery.report_paths import is_legacy_changed_path

        if data.legacy_analysis_required or any(
            is_legacy_changed_path(path) for path in data.changed_files
        ):
            raise StepFailed(
                "旧框架只允许分析报告交付，不允许提交脚本变更",
                {"reason": "legacy_analysis_only"},
            )
        if not data.delivery_eligible:
            raise StepFailed(
                "交付已阻断：尚未取得允许交付的有效审查结论",
                {"reason": "review_not_eligible"},
            )
        # 候选只写入本地分支；人工审批通过后才正式交付同一提交。
        if not data.changed_files:
            raise StepFailed(
                "交付失败：没有实际变更文件",
                {"changed_files": [], "reason": "no_changes"},
            )
        review_decision = str(data.review_decision or "").upper()
        if review_decision not in ("PASS", "HUMAN"):
            raise StepFailed(
                f"交付失败：审查未通过，decision={review_decision}",
                {"review_decision": review_decision, "reason": "review_not_passed"},
                result="failed",
            )

    # 执行交付
    if ctx.services.delivery is None:
        raise StepFailed("交付服务未装配")

    receipt_path = "delivery/operation.json"
    receipt: dict[str, object] = {}
    store = getattr(ctx, "store", None)
    if (
        store is not None
        and (store.task_dir(ctx.run.run_id, data.task_id) / receipt_path).is_file()
    ):
        receipt = store.read_artifact(ctx.run.run_id, receipt_path, data.task_id)

    def persist_delivery(value: dict[str, object]) -> None:
        if store is not None:
            store.write_artifact(ctx.run.run_id, receipt_path, value, data.task_id)

    try:
        repair_report = {}
        if store is not None and not data.analysis_only:
            from app.workflow.steps.finalize_execution.models import FinalizeInput
            from app.workflow.steps.finalize_execution.node import build_report

            report_path = "delivery/final.json"
            if (store.task_dir(ctx.run.run_id, data.task_id) / report_path).is_file():
                repair_report = store.read_artifact(
                    ctx.run.run_id, report_path, data.task_id
                )
                from app.workflow.evidence_version import evidence_version

                recorded_patch = (
                    (
                        (repair_report.get("validation") or {}).get("verify_issue")
                        or {}
                    ).get("evidence_version")
                    or {}
                ).get("patch_id")
                current_patch = evidence_version(
                    data.ai_workspace_path, list(data.changed_files)
                ).get("patch_id")
                if (
                    repair_report.get("review_decision") != data.review_decision
                    or repair_report.get("manual_confirmation_required")
                    != data.manual_confirmation_required
                    or repair_report.get("manual_confirmation_reasons")
                    != list(data.manual_confirmation_reasons)
                    or recorded_patch != current_patch
                ):
                    repair_report = {}
            if not repair_report:
                repair_report = build_report(
                    ctx,
                    FinalizeInput(
                        task_id=data.task_id,
                        carrier=data.carrier,
                        biztype=str(getattr(getattr(ctx, "task", None), "biztype", "")),
                        terminal_status="",
                        report_destination="",
                    ),
                )
                repair_report.update(
                    report_kind="repair_report",
                    result="WAITING_APPROVAL",
                    status="WAITING_APPROVAL",
                    terminal_status="",
                    terminal_step="request_approval",
                    finished_at=None,
                    terminal_reason="候选修复已准备，等待人工审批，通过后正式交付；验证情况见 validation。",
                    next_action="审阅修复代码和验证记录后完成人工审批。",
                    review_decision=data.review_decision,
                    review_summary=data.review_summary,
                    manual_confirmation_required=data.manual_confirmation_required,
                    manual_confirmation_reasons=list(data.manual_confirmation_reasons),
                    repair_branch=data.repair_branch,
                )
                store.write_artifact(
                    ctx.run.run_id, report_path, repair_report, data.task_id
                )
        result = ctx.services.delivery.deliver(
            {
                "run_id": ctx.run.run_id,
                "task_id": data.task_id,
                "repair_report": repair_report,
                "delivery_receipt": receipt,
                "persist_delivery": persist_delivery,
                "carrier": data.carrier,
                "repair_branch": data.repair_branch,
                "source_repo_path": data.source_repo_path,
                "ai_workspace_path": data.ai_workspace_path,
                "changed_files": [] if data.analysis_only else list(data.changed_files),
                "analysis_only": data.analysis_only,
                "report_paths": list(data.report_paths),
                "delivery_status": "ANALYSIS_ONLY" if data.analysis_only else "REPAIR",
                "allow_push": data.allow_push
                if data.publish or data.analysis_only
                else False,
                "require_push": data.require_push
                if data.publish or data.analysis_only
                else False,
                "error": data.error,
                "repair_analysis": data.repair_analysis,
                "repair_summary": data.repair_summary,
                "review_summary": data.review_summary,
                "manual_confirmation_required": data.manual_confirmation_required,
                "manual_confirmation_reasons": list(data.manual_confirmation_reasons),
            }
        )
    except StepFailed:
        raise
    except Exception as error:
        # 服务端保留完整堆栈；steps.json 同时保存安全的文件/行号/函数帧，
        # 以免日志未接入工作台时只剩一个 AttributeError，无法定位具体调用点。
        logger.exception(
            "修复分支交付异常 run_id=%s task_id=%s carrier=%s",
            ctx.run.run_id,
            data.task_id,
            data.carrier,
        )
        reason = public_exception(error, prefix="交付失败")
        frames = traceback.extract_tb(error.__traceback__)
        diagnostic_frames = [
            {
                "file": Path(frame.filename).name,
                "line": int(frame.lineno),
                "function": frame.name,
            }
            for frame in frames[-8:]
        ]
        raise StepFailed(
            reason,
            {
                "reason": reason,
                "exception_type": type(error).__name__,
                "commit": str(getattr(error, "commit", "") or ""),
                "delivery_error": str(getattr(error, "status", "") or ""),
                "diagnostic_frames": diagnostic_frames,
            },
            code="DELIVERY_EXECUTION_ERROR",
        ) from error

    return DeliveryOutput(
        candidate_id=candidate_id,
        binding=data.binding,
        delivery_status="ANALYSIS_ONLY"
        if data.analysis_only
        else "DELIVERED"
        if data.publish
        else "PREPARED",
        repair_branch=str(result.get("repair_branch") or data.repair_branch),
        commit=str(result.get("commit") or ""),
        pushed=bool(result.get("pushed")),
        local_branch=bool(result.get("local_branch")),
        manual_confirmation_required=data.manual_confirmation_required,
        manual_confirmation_reasons=list(data.manual_confirmation_reasons),
    )
