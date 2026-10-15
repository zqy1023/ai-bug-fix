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
        "decision": str(state.get("decision") or "PENDING"),
        "carrier": str(task.get("carrier") or ""),
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
    decision = str(output["decision"])
    return {
        "decision": decision,
        # 批准即放行；拒绝或未决都不能让交付门禁误判为已批准。
        "approval_granted": decision == "APPROVE",
        # 审批已经用掉本轮唯一的一次升级机会，后续失败不再反复申请授权。
        "escalation_used": decision == "APPROVE",
    }


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
