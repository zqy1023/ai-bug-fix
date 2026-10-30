"""创建修复分支并提交。不创建 MR，不自动合并。"""

from __future__ import annotations

import logging
import json
import traceback
from pathlib import Path
from typing import Any

from core.contracts import StepContext
from core.errors import StepFailed
from infrastructure.storage.redaction import public_exception
from .models import DeliveryInput, DeliveryOutput

logger = logging.getLogger(__name__)


def _delivery_report(value: dict[str, Any]) -> dict[str, Any]:
    """裁剪交付报告：移除内网代理地址与真实回归请求原文。

    内部审计产物（如 ``artifacts/regression_report.json``）保持原样，只有会
    进入交付面（delivery/final.json、task_summary.md）的报告副本脱敏。
    不修改调用方持有的原对象，避免影响审计与哈希核对。
    """
    import copy

    report = copy.deepcopy(value)
    _strip_delivery_payload(report)
    return report


def _strip_delivery_payload(value: Any) -> None:
    """递归删除交付报告中的环境地址与回归原始输入/脚本。"""
    if isinstance(value, dict):
        environment = value.get("environment")
        if isinstance(environment, dict):
            environment.pop("proxy_manager_address", None)
        request = value.get("request_regression")
        if isinstance(request, dict):
            request.pop("input", None)
            request.pop("script", None)
        for item in value.values():
            _strip_delivery_payload(item)
    elif isinstance(value, list):
        for item in value:
            _strip_delivery_payload(item)


def _analysis_input_fingerprint(data: DeliveryInput) -> str:
    """分析报告的稳定内容指纹，只依赖分析结论本身，不含时间戳等易变字段。

    交付重试与正式交付（publish）都必须复用同一份报告；否则 ``build_report`` 每次
    都会刷新 ``finished_at``，导致交付内容指纹漂移，同一候选被反复创建新提交，
    最终推送修复分支时 non-fast-forward 失败。
    """
    from workflow.engine.routes import fingerprint

    return fingerprint(
        {
            "carrier": data.carrier,
            "error": data.error,
            "repair_analysis": data.repair_analysis,
            "repair_summary": data.repair_summary,
            "review_summary": data.review_summary,
            "review_decision": data.review_decision,
            "report_paths": sorted(data.report_paths),
        }
    )


def run(ctx: StepContext, data: DeliveryInput) -> DeliveryOutput:
    """把通过审查的修复交付到修复分支。交付前检查必要的门禁条件。"""
    from workflow.engine.evidence_version import evidence_version
    from workflow.engine.routes import fingerprint

    candidate_id = fingerprint(
        {
            "binding": data.binding.model_dump(mode="json") if data.binding else None,
            "branch": data.repair_branch,
            "files": sorted(data.changed_files),
        }
    )
    # 正式交付（publish）必须始终绑定到准备阶段生成的候选：analysis_only 只影响
    # "交付什么内容"，不能成为跳过候选身份校验的旁路。
    if not data.analysis_only:
        if data.binding is None:
            raise StepFailed("交付缺少验证证据身份")
        current = evidence_version(data.ai_workspace_path, data.changed_files)
        if current["patch_id"] != data.binding.revision_id:
            raise StepFailed("补丁已变化，候选及验证证据已失效")
    if data.publish and data.delivery_candidate_id != candidate_id:
        raise StepFailed("交付候选不匹配，禁止正式交付")
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
        from .report_paths import is_legacy_changed_path

        if data.legacy_analysis_required:
            raise StepFailed(
                "旧框架只允许分析报告交付，不允许提交脚本变更",
                {"reason": "legacy_analysis_only"},
            )
        if not data.delivery_eligible:
            raise StepFailed(
                "交付已阻断：尚未取得允许交付的有效审查结论",
                {"reason": "review_not_eligible"},
            )
        # 候选先锚定到源仓库的交付锚点（不建本地分支），随后由 publish 正式交付同一提交。
        if not data.changed_files:
            raise StepFailed(
                "交付失败：没有实际变更文件",
                {"changed_files": [], "reason": "no_changes"},
            )
        review_decision = str(data.review_decision or "").upper()
        # 是否允许交付由 review_agent 的三项审查结论决定；MANUAL 可能仅表示
        # 缺少录制 response，不能在此重复把业务未验证变成交付失败。
        if review_decision not in ("PASS", "HUMAN", "MANUAL"):
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
        if data.analysis_only:
            from workflow.stages.finalize.models import FinalizeInput
            from workflow.stages.finalize.node import build_report
            from infrastructure.workspace.workspace import WorkspaceManager

            if store is None:
                raise StepFailed("分析报告生成失败：未配置记录仓库")
            workspace = Path(data.ai_workspace_path).resolve()
            carrier = WorkspaceManager.safe_carrier_name(data.carrier).casefold()
            # 只生成当前承运商目录中的约定报告，不向任意输入路径写入。
            for relative in data.report_paths:
                target = (workspace / relative).resolve()
                parts = Path(relative).parts
                if (not target.is_relative_to(workspace) or len(parts) != 3
                    or parts[0] not in {"gathers", "spiders"}
                    or parts[1].casefold() != carrier or parts[2] != "final.json"):
                    raise StepFailed("分析报告目标路径不合法")
            # 交付重试与正式交付（publish）必须复用同一份报告：build_report 每次
            # 都会刷新 finished_at，若不缓存，交付内容指纹会随重试漂移，同一候选
            # 被反复创建新提交，最终推送修复分支时 non-fast-forward 失败。
            input_fingerprint = _analysis_input_fingerprint(data)
            report_path = "delivery/analysis.json"
            fingerprint_path = "delivery/analysis.input_fingerprint.json"
            report = None
            task_dir = store.task_dir(ctx.run.run_id, data.task_id)
            if (task_dir / report_path).is_file() and (task_dir / fingerprint_path).is_file():
                cached = store.read_artifact(ctx.run.run_id, fingerprint_path, data.task_id)
                if isinstance(cached, dict) and cached.get("fingerprint") == input_fingerprint:
                    report = store.read_artifact(ctx.run.run_id, report_path, data.task_id)
            if report is None:
                report = _delivery_report(build_report(ctx, FinalizeInput(
                    task_id=data.task_id, carrier=data.carrier,
                    biztype=str(getattr(getattr(ctx, "task", None), "biztype", "")),
                    terminal_status="manual", report_destination="",
                )))
                store.write_artifact(ctx.run.run_id, fingerprint_path, {"fingerprint": input_fingerprint}, data.task_id)
            for relative in data.report_paths:
                target = (workspace / relative).resolve()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            store.write_artifact(ctx.run.run_id, report_path, report, data.task_id)
        if store is not None and not data.analysis_only:
            from workflow.stages.finalize.models import FinalizeInput
            from workflow.stages.finalize.node import build_report

            report_path = "delivery/final.json"
            if (store.task_dir(ctx.run.run_id, data.task_id) / report_path).is_file():
                repair_report = store.read_artifact(
                    ctx.run.run_id, report_path, data.task_id
                )
                from workflow.engine.evidence_version import evidence_version

                recorded_patch = ((repair_report.get("review") or {}).get("evidence_version") or {}).get("patch_id")
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
                # 分支携带的是不可变审查快照。发布结果由 finalize 报告结算，
                # 不能在准备时宣称已发布，也不能等待主图中不存在的审批节点。
                repair_report.update(
                    report_kind="repair_review",
                    result=data.review_decision.upper(),
                    status=data.review_decision.upper(),
                    terminal_status="",
                    terminal_step="review_agent",
                    terminal_reason="代码审查已完成；交付结果见工作台最终报告。",
                    next_action="在 Git 上检查补丁及审查限制，按团队流程复核与合并。",
                    review_decision=data.review_decision,
                    review_summary=data.review_summary,
                    manual_confirmation_required=data.manual_confirmation_required,
                    manual_confirmation_reasons=list(data.manual_confirmation_reasons),
                    repair_branch=data.repair_branch,
                )
                repair_report.pop("resolution", None)
            store.write_artifact(
                ctx.run.run_id, report_path, _delivery_report(repair_report), data.task_id
            )
        # 交付面副本与后续 task_summary 都使用脱敏后的报告；内部审计产物不动。
        repair_report = _delivery_report(repair_report)
        result = ctx.services.delivery.deliver(
            {
                "run_id": ctx.run.run_id,
                "agent_policy": data.agent_policy,
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
                "delivery_phase": "ANALYSIS_ONLY" if data.analysis_only else "REPAIR",
                "allow_push": data.allow_push
                if data.publish
                else False,
                "require_push": data.require_push
                if data.publish
                else False,
                # 只有正式交付阶段（publish）在无法推送时才落本地分支；
                # 准备阶段只保留交付锚点，不制造第二套本地分支状态。
                "materialize_local_branch": bool(data.publish),
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
        delivery_phase="ANALYSIS_ONLY"
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

