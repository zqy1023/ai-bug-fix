"""创建修复分支、提交并按配置推送。"""

from __future__ import annotations

from typing import Any

from app.domain.keys import repair_branch
from app.infrastructure.filesystem.workspace import WorkspaceManager
from app.workflow.spec import Dependency, StepSpec
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
    # 审查结论以主图通道为准。review_agent 回写的是 verdict 而不是 result，
    # 且只有审查子图实际跑过才有结论；人工审查模式没有子图，结论记在 decision 上。
    # review_agent 现在始终先执行；人工审批统一放在分支交付之后。
    review_decision = str(review_payload.get("verdict") or "")
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
        "authorized_files": [str(item) for item in state.get("authorized_files") or []],
        "error": str(problem_payload.get("error") or ""),
        "repair_analysis": str(repair_payload.get("analysis") or ""),
        "repair_summary": str(repair_payload.get("summary") or ""),
        "review_summary": str(review_payload.get("summary") or ""),
        "review_decision": review_decision,
        "analysis_only": bool(
            (state.get("evidence") or {}).get("legacy_only")
            or (state.get("evidence") or {}).get("legacy_dependency")
            or state.get("scope_escalation_required")
            or state.get("manual_intervention_required")
            or str((state.get("review") or {}).get("verdict") or "").upper() == "MANUAL"
            or str((state.get("task_summary") or {}).get("delivery_status") or "").upper() in {"FAILED", "MANUAL"}
            or str((state.get("locate") or {}).get("status") or "") != "LOCATED"
            or str((state.get("locate") or {}).get("recommended_scope") or "") != "carrier"
            or str((state.get("locate") or {}).get("category") or "") != "carrier"
            or any(
                str(item.get("path") or "").replace("\\", "/").startswith("spiders/")
                for item in ((state.get("locate") or {}).get("source_evidence") or [])
                if isinstance(item, dict)
            )
            or any(
                str(item).replace("\\", "/").startswith("spiders/")
                for item in ((state.get("locate") or {}).get("candidate_files") or [])
            )
        ),
        "report_paths": [f"{report_framework}/{report_carrier}/final.json"],
        # 需要审批 = 本轮存在已获批或待审批的共享层方案；是否放行只看批准结果。
        "requires_approval": bool(state.get("scope_escalation_required")),
        "approval_granted": str(state.get("decision") or "") == "APPROVE",
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "delivery": dict(output),
        "repair_branch": str(output.get("repair_branch") or ""),
        "delivery_pushed": bool(output.get("pushed")),
        "delivery_commit": str(output.get("commit") or ""),
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
