"""步骤完整产物与状态交接的唯一写入边界。"""

from __future__ import annotations

from typing import Any

from app.workflow.contracts import (
    Assessment,
    ExecutionError,
    ExecutionResult,
    Handoff,
    StepOutcome,
    TaskSnapshot,
)

# 这些通道仍是现有执行器的输入适配，失效规则只在此维护。
VALIDATION_KEYS = (
    "validation_plan",
    "validation_summary",
    "validation_decision",
    "verify_result",
    "regression_result",
    "review",
)
CANDIDATE_KEYS = ("delivery_candidate", "approval_result", "delivery")
INVALIDATES = {
    "prepare_source_snapshot": (
        "locate",
        "acceptance_contract",
        "baseline_reproduction",
        *VALIDATION_KEYS,
        *CANDIDATE_KEYS,
    ),
    "locate_agent": (
        "acceptance_contract",
        "baseline_reproduction",
        *VALIDATION_KEYS,
        *CANDIDATE_KEYS,
    ),
    "define_acceptance": ("baseline_reproduction", *VALIDATION_KEYS, *CANDIDATE_KEYS),
    "repair_agent": ("gate_result", "syntax_result", *VALIDATION_KEYS, *CANDIDATE_KEYS),
    "check_changes": ("syntax_result", *VALIDATION_KEYS, *CANDIDATE_KEYS),
    "plan_validation": (
        "validation_summary",
        "validation_decision",
        "verify_result",
        "regression_result",
        "review",
        *CANDIDATE_KEYS,
    ),
    "verify_issue": (
        "validation_summary",
        "validation_decision",
        "review",
        *CANDIDATE_KEYS,
    ),
    "run_regression": (
        "validation_summary",
        "validation_decision",
        "review",
        *CANDIDATE_KEYS,
    ),
    "review_agent": ("validation_decision", *CANDIDATE_KEYS),
    "prepare_delivery": ("approval_result", "delivery"),
}

# 通道唯一属主：写入校验与"清空即失效"共用同一张表，避免两份手工同步的映射漂移。
# EXEMPT_FROM_OBSOLETE 中的通道被清空时只重置数据，不撤销属主步骤的结论记录——
# PREPARE_CONTEXT 重试清空 baseline_* 属于"准备重做"，不代表快照结论失效。
STATE_OWNERS = {
    "baseline_dir": "prepare_source_snapshot",
    "baseline_manifest": "prepare_source_snapshot",
    "locate": "locate_agent",
    "acceptance_contract": "define_acceptance",
    "baseline_reproduction": "reproduce_baseline",
    "repair": "repair_agent",
    "gate_result": "check_changes",
    "syntax_result": "syntax_regression",
    "validation_plan": "plan_validation",
    "verify_result": "verify_issue",
    "regression_result": "run_regression",
    "validation_summary": "summarize_validation",
    "validation_decision": "decide_validation",
    "review": "review_agent",
    "delivery_candidate": "prepare_delivery",
    "approval_result": "request_approval",
    "delivery": "publish_delivery",
    # 以下通道的属主由实际写入点确定：repair_branch 由 prepare_delivery 生成；
    # delivery_pushed 的终态只由 publish_delivery 写入（prepare_delivery 只做候选重置）。
    "repair_branch": "prepare_delivery",
    "delivery_pushed": "publish_delivery",
    "delivery_local_branch": "prepare_delivery",
    "analysis_report": "prepare_analysis",
    "task_summary": "finalize_execution",
    "resolution": "finalize_execution",
}
# 候选阶段需要重置交付通道；这些步骤不是属主，但必须允许写入。
STATE_CO_WRITERS = {
    "delivery_pushed": frozenset({"prepare_delivery"}),
    # 交付后的提交形态以 publish_delivery 为准（决定是否推送到远端）。
    "delivery_local_branch": frozenset({"publish_delivery"}),
}
EXEMPT_FROM_OBSOLETE = frozenset({"baseline_dir", "baseline_manifest"})


def outcome_of(
    output: dict[str, Any],
    *,
    execution_status: str = "succeeded",
    error: dict[str, Any] | None = None,
    summary: str = "",
    business: str | None = None,
    artifacts: list[str] | None = None,
) -> StepOutcome:
    """将插件业务模型转为统一结果，执行失败绝不视为代码缺陷。"""
    status = output.get("status")
    if execution_status != "succeeded":
        outcome = "inconclusive"
    elif status in {
        "unverified",
        "not_configured",
        "not_executed",
        "syntax_only",
    } or business in {"MANUAL", "INSUFFICIENT_EVIDENCE", "UNVERIFIED", "BLOCKED"}:
        outcome = "inconclusive"
    elif output.get("action") == "blocked" or output.get("stale") is True:
        outcome = "inconclusive"
    elif isinstance(output.get("report"), dict) and output["report"].get("results"):
        statuses = {item.get("status") for item in output["report"]["results"]}
        outcome = (
            "failed"
            if "failed" in statuses
            else "inconclusive"
            if "unverified" in statuses
            else "passed"
        )
    elif business == "SKIPPED":
        outcome = "not_applicable"
    elif output.get("passed") is False or business in {"FAIL", "FAILED", "REJECTED"}:
        outcome = "failed"
    else:
        outcome = "passed"
    return StepOutcome(
        execution=ExecutionResult(
            status=execution_status,
            error=ExecutionError.model_validate(error) if error else None,
        ),
        assessment=Assessment(
            outcome=outcome,
            code=str(output.get("reason_code") or business or status or outcome),
            summary=summary or str(output.get("reason") or output.get("summary") or ""),
            findings=[str(item) for item in output.get("findings", [])],
        ),
        output=output,
        artifact_paths=artifacts or [],
    )


def apply_step_result(
    store: Any,
    state: dict[str, Any],
    step: str,
    patch: dict[str, Any],
    outcome: StepOutcome,
) -> dict[str, Any]:
    """先保存无损交接，再发布图更新；摘要记录不承担恢复职责。"""
    for key, value in patch.items():
        if (
            key in STATE_OWNERS
            and STATE_OWNERS[key] != step
            and step not in STATE_CO_WRITERS.get(key, ())
            # retry_prepare 是内部重置节点：新一轮尝试必须能清空旧通道。
            and not (step == "retry_prepare" and (not value or value == state.get(key)))
        ):
            raise ValueError(f"STATE_OWNER_MISMATCH: {step} 不能写入 {key}")
    invalidated = {key: {} for key in INVALIDATES.get(step, ()) if key not in patch}
    if any(key in invalidated for key in CANDIDATE_KEYS):
        invalidated.update(approval_granted=False, decision="PENDING")
    patch = {**invalidated, **patch}
    if step != "finalize_execution":
        patch.update(resolution=None, task_summary={})
    current = {**state, **patch}
    current.pop("step_result", None)
    current.pop("_single_step", None)
    obsolete = {
        STATE_OWNERS[key]
        for key in patch
        if key in STATE_OWNERS
        and key not in EXEMPT_FROM_OBSOLETE
        and not patch[key]
    }
    outcomes = {
        key: value
        for key, value in (state.get("step_outcomes") or {}).items()
        if key not in obsolete
    }
    outcomes[step] = outcome.model_dump(mode="json")
    current["step_outcomes"] = outcomes
    run_id, task_id = str(state["run_id"]), str(state.get("task_id") or "")
    handoff = Handoff(
        step=step, outcome=outcome, state=TaskSnapshot.from_graph(current)
    )
    path = f"workflow/outputs/{step}.json"
    store.write_artifact(run_id, path, handoff.model_dump(mode="json"), task_id)
    store.write_artifact(
        run_id, "workflow/state.json", handoff.model_dump(mode="json"), task_id
    )
    return {**patch, "step_outcomes": outcomes}
