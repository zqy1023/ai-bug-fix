"""汇总生成报告与交付物。

根据是否有代码修改，统一准备文件改动+流程报告或纯流程报告。
"""

from __future__ import annotations

from typing import Any

from core.errors import StepFailed
from core.keys import repair_branch
from infrastructure.workspace.workspace import WorkspaceManager
from core.contracts import StepSpec
from .models import DeliveryInput, DeliveryOutput
from .node import run
from config.settings import settings


def _input(state: dict[str, Any]) -> dict[str, Any]:
    task = dict(state.get("task") or {})
    task_id = str(state.get("task_id") or "")
    unified = state.get("unified_fixer")
    unified_payload = dict(unified) if isinstance(unified, dict) else {}
    review = state.get("review")
    review_payload = dict(review) if isinstance(review, dict) else {}
    problem = state.get("problem")
    problem_payload = dict(problem) if isinstance(problem, dict) else {}

    review_mode = str(state.get("review_mode") or settings.default_review_mode).lower()
    review_decision = str(review_payload.get("verdict") or "")
    if review_mode == "human" and review_decision.upper() not in {"PASS", "FAIL"}:
        review_decision = "HUMAN"
    evidence = dict(state.get("evidence") or {})
    locate_payload = unified_payload

    from .report_paths import report_dir, report_framework

    report_framework_name = report_framework(evidence, locate_payload, state.get("framework"))
    report_carrier = WorkspaceManager.safe_carrier_name(str(task.get("carrier") or ""))
    report_directory = report_dir(evidence, report_framework_name, report_carrier)
    from workflow.engine.routes import _analysis_only_task

    analysis_only = _analysis_only_task(state)
    from core.policy import legacy_analysis_required
    from workflow.engine.confirmation import confirmation

    manual_required, manual_reasons = confirmation(state)
    from core.policy import policy_of
    policy = policy_of(state)
    biztype = str(task.get("biztype") or "")
    carrier = str(task.get("carrier") or "")
    problem_key = str(task.get("problem_key") or "")
    branch = str(state.get("repair_branch") or "")
    if not branch and (biztype or carrier):
        branch = repair_branch(biztype, carrier, settings.repair_branch_prefix, problem_key)
    binding = None
    if not analysis_only:
        from infrastructure.workspace.diff import source_changes
        if not state.get("baseline_dir") or source_changes(
            str(state.get("ai_workspace_path") or ""), str(state["baseline_dir"])
        ) != sorted(set(state.get("changed_files") or [])):
            raise StepFailed("变更清单与源码基线不一致", code="CANDIDATE_FILES_STALE")
        from agents.review.contract import REVIEW_CONTRACT_VERSION, delivery_allowed
        from workflow.engine.evidence_version import evidence_version
        from workflow.engine.routes import fingerprint

        version = evidence_version(
            str(state.get("ai_workspace_path") or ""),
            list(state.get("changed_files") or []),
            str(state.get("baseline_dir") or ""),
        )
        if (
            # MANUAL 表示"代码没发现问题、但业务回归未验证"，契约仍然有效：
            # 这里只要求结构合法，是否放行由 delivery_eligible + 人工确认决定。
            # 缺少录制 response 不应该把已经修对的代码卡在分析报告阶段。
            not delivery_allowed(review_payload)
            or review_payload.get("review_contract_version") != REVIEW_CONTRACT_VERSION
            or review_payload.get("evidence_version") != version
        ):
            raise StepFailed(
                "CANDIDATE_EVIDENCE_STALE: 缺少当前补丁对应的有效 Agent 审查结论",
                code="CANDIDATE_EVIDENCE_STALE",
            )
        binding = {
            "problem_id": fingerprint(problem_payload),
            "baseline_id": version["baseline_id"],
            "acceptance_id": fingerprint({
                "review": review_payload,
                "acceptance_criteria": unified_payload.get("acceptance_criteria") or [],
                "manual_confirmation_required": manual_required,
            }),
            "revision_id": version["patch_id"],
            "policy_id": fingerprint({"review_contract_version": REVIEW_CONTRACT_VERSION,
                                      "agent_policy": policy.model_dump(mode="json")}),
        }
    return {
        "agent_policy": policy.model_dump(mode="json"),
        "publish": False,
        "delivery_candidate_id": "",
        "binding": binding,
        "task_id": task_id,
        "carrier": carrier,
        "repair_branch": branch,
        "source_repo_path": str(state.get("source_repo_path") or ""),
        "ai_workspace_path": str(state.get("ai_workspace_path") or ""),
        "changed_files": list(state.get("changed_files") or []),
        "error": str(problem_payload.get("error") or ""),
        "repair_analysis": str(unified_payload.get("analysis") or ""),
        "repair_summary": str(unified_payload.get("summary") or ""),
        "review_summary": str(review_payload.get("summary") or ""),
        "delivery_eligible": not analysis_only and delivery_allowed(review_payload)
        and not state.get("scope_escalation_required"),
        "review_decision": review_decision,
        "legacy_analysis_required": legacy_analysis_required(state),
        "analysis_only": analysis_only,
        "report_paths": [f"{report_directory}/final.json"] if analysis_only else [],
        "allow_push": policy.remote_push and bool(state.get("allow_push", state.get("git_push", False))),
        "require_push": bool(state.get("require_push", False)),
        "manual_confirmation_required": manual_required,
        "manual_confirmation_reasons": manual_reasons,
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "delivery_candidate": dict(output),
        "repair_branch": str(output.get("repair_branch") or ""),
        "delivery_pushed": bool(output.get("pushed")),
        "delivery_local_branch": bool(output.get("local_branch")),
        "delivery_commit": str(output.get("commit") or ""),
        "manual_confirmation_required": bool(
            output.get("manual_confirmation_required")
        ),
        "manual_confirmation_reasons": list(
            output.get("manual_confirmation_reasons") or []
        ),
    }


SPEC = StepSpec(
    name="generate_report",
    title="汇总生成报告与交付物",
    runner=run,
    input_model=DeliveryInput,
    output_model=DeliveryOutput,
    input_builder=_input,
    output_patch=_patch,
)

__all__ = ["SPEC", "_input", "_patch"]
