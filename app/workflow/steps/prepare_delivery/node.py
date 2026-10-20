"""创建修复分支并提交。不创建 MR，不自动合并。"""

from __future__ import annotations

import logging
import traceback
from pathlib import Path

from app.domain.errors import StepFailed
from app.application.context import StepContext
from app.workflow.steps.prepare_delivery.models import DeliveryInput, DeliveryOutput
from app.infrastructure.redaction import public_exception

logger = logging.getLogger(__name__)


def run(ctx: StepContext, data: DeliveryInput) -> DeliveryOutput:
    """把通过审查的修复交付到修复分支。交付前检查必要的门禁条件。"""

    if data.analysis_only:
        if not data.report_paths:
            raise StepFailed("分析任务交付失败：缺少最终报告", {"reason": "report_missing"})
    else:
        # 源码交付必须有实际改动。PASS 表示 AI 审查通过；MANUAL 表示 AI
        # 明确要求人工复核。两者都要先提交候选分支，再由最终审批决定保留或删除。
        # HUMAN 则表示配置为人工审查模式，因此跳过 AI 审查。
        if not data.changed_files:
            raise StepFailed("交付失败：没有实际变更文件", {"changed_files": [], "reason": "no_changes"})
        review_decision = str(data.review_decision or "").upper()
        if review_decision not in ("PASS", "APPROVED", "MANUAL", "HUMAN"):
            raise StepFailed(
                f"交付失败：审查未通过，decision={review_decision}",
                {"review_decision": review_decision, "reason": "review_not_passed"},
                result="failed",
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
                "changed_files": [] if data.analysis_only else list(data.changed_files),
                "analysis_only": data.analysis_only,
                "report_paths": list(data.report_paths),
                "delivery_status": "ANALYSIS_ONLY" if data.analysis_only else "REPAIR",
                "allow_push": data.allow_push,
                "require_push": data.require_push,
                "error": data.error,
                "repair_analysis": data.repair_analysis,
                "repair_summary": data.repair_summary,
                "review_summary": data.review_summary,
            }
        )
    except StepFailed:
        raise
    except Exception as error:
        # 服务端保留完整堆栈；steps.json 同时保存安全的文件/行号/函数帧，
        # 以免日志未接入工作台时只剩一个 AttributeError，无法定位具体调用点。
        logger.exception(
            "修复分支交付异常 run_id=%s task_id=%s carrier=%s",
            ctx.run.run_id, data.task_id, data.carrier,
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
                "diagnostic_frames": diagnostic_frames,
            },
            code="DELIVERY_EXECUTION_ERROR",
        ) from error

    return DeliveryOutput(
        repair_branch=str(result.get("repair_branch") or data.repair_branch),
        commit=str(result.get("commit") or ""),
        pushed=bool(result.get("pushed")),
        local_branch=bool(result.get("local_branch")),
    )


