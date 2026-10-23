"""创建修复分支、提交并按配置推送。"""

from __future__ import annotations

from typing import Any

from app.domain.keys import repair_branch
from app.infrastructure.filesystem.workspace import WorkspaceManager
from app.workflow.spec import StepSpec
from app.workflow.steps.prepare_delivery.models import DeliveryInput, DeliveryOutput
from config.settings import settings
from app.workflow.steps.prepare_delivery.node import run


def _input(state: dict[str, Any]) -> dict[str, Any]:
    task = dict(state.get("task") or {})
    task_id = str(state.get("task_id") or "")
    repair = state.get("repair")
    repair_payload = dict(repair) if isinstance(repair, dict) else {}
    review = state.get("review")
    review_payload = dict(review) if isinstance(review, dict) else {}
    problem = state.get("problem")
    problem_payload = dict(problem) if isinstance(problem, dict) else {}
    # 审查结论以主图通道为准。review_agent 回写的是 verdict 而不是 result；
    # human 模式跳过 AI 审查直接交付分支，交付门禁由人工在审批节点判定，
    # 因此这里用 HUMAN 标记"未经 AI 审查"，不能让空结论被当成审查未通过。
    review_mode = str(state.get("review_mode") or settings.default_review_mode).lower()
    review_decision = str(review_payload.get("verdict") or "")
    if review_mode == "human" and not review_decision:
        review_decision = "HUMAN"
    evidence = dict(state.get("evidence") or {})
    locate_payload = dict(state.get("locate") or {})
    legacy_reference = any(
        str(item.get("path") or "").replace("\\", "/").startswith("spiders/")
        for item in locate_payload.get("source_evidence") or []
        if isinstance(item, dict)
    ) or any(
        str(item).replace("\\", "/").startswith("spiders/")
        for item in locate_payload.get("candidate_files") or []
    )
    report_framework = (
        "spiders"
        if evidence.get("legacy_only") or evidence.get("legacy_dependency") or legacy_reference
        else str(state.get("framework") or "gathers")
    )
    report_carrier = WorkspaceManager.safe_carrier_name(str(task.get("carrier") or ""))
    # 源码目录可能保留原始大小写（gathers/Airpak）。必须用证据里的真实路径，
    # 否则在大小写敏感的文件系统上会交付一个与 final.json 实际位置不同的路径。
    report_dir = str(evidence.get("carrier_dir") or "").replace("\\", "/").strip("/") or (
        f"{report_framework}/{report_carrier}"
    )
    from app.workflow.routes import _analysis_only_task
    analysis_only = _analysis_only_task(state)
    from app.domain.repair_scope import legacy_analysis_required
    from app.workflow.confirmation import confirmation
    manual_required, manual_reasons = confirmation(state)
    return {
        "task_id": task_id,
        "carrier": str(task.get("carrier") or ""),
        "repair_branch": str(
            task.get("repair_branch")
            or repair_branch(
                str(task.get("biztype") or ""),
                str(task.get("carrier") or ""),
                settings.repair_branch_prefix,
                str(
                    task.get("problem_key")
                    or problem_payload.get("fingerprint")
                    or task_id.rsplit("__", 1)[-1]
                ),
            )
        ),
        "source_repo_path": str(state.get("source_repo_path") or ""),
        "ai_workspace_path": str(state.get("ai_workspace_path") or ""),
        "changed_files": [str(item) for item in state.get("changed_files") or []],
        "error": str(problem_payload.get("error") or ""),
        "repair_analysis": str(repair_payload.get("analysis") or ""),
        "repair_summary": str(repair_payload.get("summary") or ""),
        "review_summary": str(review_payload.get("summary") or ""),
        "review_decision": review_decision,
        "delivery_eligible": (state.get("validation_decision") or {}).get("delivery_eligible") is True,
        "legacy_analysis_required": legacy_analysis_required(state),
        "analysis_only": analysis_only,
        "report_paths": [f"{report_dir}/final.json"] if analysis_only else [],
        "allow_push": bool(state.get("allow_push", state.get("git_push", False))),
        "require_push": bool(state.get("require_push", False)),
        "manual_confirmation_required": manual_required,
        "manual_confirmation_reasons": manual_reasons,
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "delivery": dict(output),
        "repair_branch": str(output.get("repair_branch") or ""),
        "delivery_pushed": bool(output.get("pushed")),
        "delivery_local_branch": bool(output.get("local_branch")),
        "delivery_commit": str(output.get("commit") or ""),
        "manual_confirmation_required": bool(output.get("manual_confirmation_required")),
        "manual_confirmation_reasons": list(output.get("manual_confirmation_reasons") or []),
    }


SPEC = StepSpec(
    name="prepare_delivery",
    title="创建修复分支",
    runner=run,
    input_model=DeliveryInput,
    output_model=DeliveryOutput,
    input_builder=_input,
    output_patch=_patch,
    next_step="finalize_execution",
)

__all__ = ["SPEC"]
