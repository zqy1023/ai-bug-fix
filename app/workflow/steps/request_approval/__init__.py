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
    from app.workflow.confirmation import confirmation
    manual_required, manual_reasons = confirmation(state)
    return {
        "task_id": str(state.get("task_id") or ""),
        "decision": str(state.get("decision") or "PENDING").upper(),
        "reason": str(state.get("retry_reason") or ""),
        "retry_target": str(state.get("retry_target") or "") or None,
        "carrier": str(task.get("carrier") or ""),
        "source_repo_path": str(state.get("source_repo_path") or ""),
        "repair_branch": str(state.get("repair_branch") or ""),
        "delivery_pushed": bool(state.get("delivery_pushed")),
        "delivery_local_branch": bool(state.get("delivery_local_branch")),
        "manual_confirmation_required": manual_required,
        "manual_confirmation_reasons": manual_reasons,
        "ai_workspace_path": str(state.get("ai_workspace_path") or ""),
        "changed_files": [str(item) for item in state.get("changed_files") or []],
        "baseline_manifest": dict(state.get("baseline_manifest") or {}),
        "baseline_dir": str(state.get("baseline_dir") or ""),
        "approval_fingerprint": hashlib.sha256(
            json.dumps(
                {
                    "task_id": str(state.get("task_id") or ""),
                    "change_plan": state.get("change_plan") or {},
                    "changed_files": sorted(str(item) for item in state.get("changed_files") or []),
                    "baseline_manifest": state.get("baseline_manifest") or {},
                    "manual_confirmation_required": manual_required,
                    "manual_confirmation_reasons": manual_reasons,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    decision = str(output["decision"]).upper()
    return {
        "decision": decision,
        "approval_granted": decision == "APPROVE",
        "approval_cleanup_failed": bool(output.get("approval_cleanup_failed")),
        "manual_confirmation_required": bool(output.get("manual_confirmation_required")),
    }


SPEC = StepSpec(
    name="request_approval",
    title="人工审批",
    runner=run,
    input_model=ApprovalInput,
    output_model=ApprovalOutput,
    input_builder=_input,
    output_patch=_patch,
    # 只依赖交付：human 模式跳过 AI 审查，没有 review_agent 记录也要能进入审批。
    depends_on=(Dependency("prepare_delivery", "task"),),
    next_step="finalize_execution",
)

__all__ = ["SPEC"]
