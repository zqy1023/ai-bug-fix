"""人工审批暂停和恢复，以及最终门禁。"""

from __future__ import annotations

from typing import Any
import hashlib
import json

from app.workflow.spec import Dependency, StepSpec
from app.workflow.steps.request_approval.models import ApprovalInput, ApprovalOutput
from app.workflow.steps.request_approval.node import run


def _input(state: dict[str, Any]) -> dict[str, Any]:
    task = dict(state.get("task") or {})
    return {
        "task_id": str(state.get("task_id") or ""),
        # 人工处置决定只用于当前 interrupt 路由；到达下一次审批节点时必须重新等待新决定。
        "decision": "PENDING" if str(state.get("decision") or "").upper() in {"RETRY_LOCATE", "CONTINUE_REPAIR", "MARK_DONE", "TERMINATE"} else str(state.get("decision") or "PENDING"),
        "mode": "manual_intervention" if state.get("manual_intervention_required") else "change_approval",
        "intervention_reason": str(state.get("manual_intervention_reason") or (state.get("locate") or {}).get("summary") or ""),
        "intervention_step": str(state.get("manual_intervention_step") or "locate_agent"),
        "candidate_files": [str(item) for item in (state.get("locate") or {}).get("candidate_files") or []],
        "root_cause": str((state.get("locate") or {}).get("root_cause") or ""),
        "continue_allowed": bool(state.get("manual_continue_allowed")),
        "recommended_scope": str((state.get("locate") or {}).get("recommended_scope") or "manual"),
        "carrier": str(task.get("carrier") or ""),
        "source_repo_path": str(state.get("source_repo_path") or ""),
        "repair_branch": str(state.get("repair_branch") or ""),
        "delivery_pushed": bool(state.get("delivery_pushed")),
        "ai_workspace_path": str(state.get("ai_workspace_path") or ""),
        "changed_files": [str(item) for item in state.get("changed_files") or []],
        "baseline_manifest": dict(state.get("baseline_manifest") or {}),
        "baseline_dir": str(state.get("baseline_dir") or ""),
        "authorized_files": list(state.get("authorized_files") or []),
        "approval_fingerprint": hashlib.sha256(
            json.dumps(
                {
                    "task_id": str(state.get("task_id") or ""),
                    "change_plan": state.get("change_plan") or {},
                    "changed_files": sorted(str(item) for item in state.get("changed_files") or []),
                    "authorized_files": sorted(str(item) for item in state.get("authorized_files") or []),
                    "baseline_manifest": state.get("baseline_manifest") or {},
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    decision = str(output["decision"]).upper()
    manual_actions = {"RETRY_LOCATE", "CONTINUE_REPAIR", "MARK_DONE", "TERMINATE"}
    patch = {
        "decision": decision,
        "approval_mode": "manual_intervention" if decision in manual_actions else "change_approval",
        # 只有实际的方案批准才算共享层升级授权。
        "approval_granted": decision == "APPROVE",
        "escalation_used": decision == "APPROVE",
    }
    if decision == "RETRY_LOCATE":
        patch.update({
            "manual_intervention_required": False,
            "manual_continue_allowed": False,
            "manual_continue_authorized": False,
            "manual_intervention_reason": "",
            "manual_intervention_step": "",
            "manual_resume_step": "",
            "terminal_status": "",
            "last_result": "passed",
        })
    elif decision == "CONTINUE_REPAIR":
        # 仅在本次人工决定后开启一次授权；修复节点完成后会立即清除。
        patch.update({
            "manual_intervention_required": False,
            "manual_continue_authorized": True,
            "terminal_status": "",
            "last_result": "passed",
        })
    elif decision in {"MARK_DONE", "TERMINATE"}:
        patch.update({"manual_intervention_required": False, "terminal_status": "MANUAL", "last_result": "manual"})
    return patch


SPEC = StepSpec(
    name="request_approval",
    title="人工审批",
    runner=run,
    input_model=ApprovalInput,
    output_model=ApprovalOutput,
    input_builder=_input,
    output_patch=_patch,
    depends_on=(Dependency("review_agent", "task"),),
    next_step="apply_plan",
)

__all__ = ["SPEC"]
