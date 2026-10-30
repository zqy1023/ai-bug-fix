"""交付后的人工复核：确认保留还是打回撤回，以及最终门禁。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from core.errors import StepFailed
from workflow.engine.evidence_version import evidence_version
from core.contracts import StepSpec
from .models import ApprovalInput, ApprovalOutput
from .node import run


def _input(state: dict[str, Any]) -> dict[str, Any]:
    task = dict(state.get("task") or {})
    from workflow.engine.confirmation import confirmation

    # 审批发生在交付之后：审批身份以正式交付结果为准；旧顺序（审批→交付）
    # 挂起的任务恢复时还没有交付记录，回退到准备阶段的候选。
    candidate = state.get("delivery")
    if not isinstance(candidate, dict) or not candidate.get("candidate_id"):
        candidate = state.get("delivery_candidate")
    if not isinstance(candidate, dict) or not candidate.get("candidate_id"):
        raise StepFailed(
            "审批上下文缺失: delivery_candidate", code="APPROVAL_CONTEXT_MISSING"
        )
    if not str(candidate.get("commit") or "").strip():
        raise StepFailed(
            "审批上下文缺失: 交付提交", code="APPROVAL_CONTEXT_MISSING"
        )
    workspace_path = str(state.get("ai_workspace_path") or "")
    baseline_dir = str(state.get("baseline_dir") or "")
    if not workspace_path or not baseline_dir:
        raise StepFailed(
            "审批上下文缺失: ai_workspace_path/baseline_dir",
            code="APPROVAL_CONTEXT_MISSING",
        )
    manual_required, manual_reasons = confirmation(state)
    return {
        "task_id": str(state.get("task_id") or ""),
        "agent_policy": dict(state.get("agent_policy") or {}),
        "candidate_id": candidate["candidate_id"],
        "evidence_version": evidence_version(workspace_path, list(state.get("changed_files") or []), baseline_dir),
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
        "ai_workspace_path": workspace_path,
        "changed_files": [str(item) for item in state.get("changed_files") or []],
        "baseline_manifest": dict(state.get("baseline_manifest") or {}),
        "baseline_dir": baseline_dir,
        "approval_fingerprint": hashlib.sha256(
            json.dumps(
                {
                    "task_id": str(state.get("task_id") or ""),
                    "candidate": candidate,
                    "patch": evidence_version(
                        workspace_path,
                        state.get("changed_files") or [],
                        baseline_dir,
                    ),
                    "change_plan": state.get("change_plan") or {},
                    "changed_files": sorted(
                        str(item) for item in state.get("changed_files") or []
                    ),
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
    # 步骤没有产出审批决定（被取消或执行失败）：不回写任何状态。
    if "decision" not in output:
        return {}
    decision = str(output.get("decision") or "").upper()
    return {
        "decision": decision,
        "approval_result": dict(output),
        "approval_granted": decision == "APPROVE",
        "approval_cleanup_failed": bool(output.get("approval_cleanup_failed")),
        "manual_confirmation_required": bool(
            output.get("manual_confirmation_required")
        ),
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
)

__all__ = ["SPEC"]

