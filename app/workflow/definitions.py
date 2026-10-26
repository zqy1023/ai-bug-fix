"""权威工作流定义。

图层只保留业务阶段、Agent、安全门禁、审批与交付节点。
组合阶段内部仍调用原始步骤插件，因此步骤级调试、审计和产物记录不变。
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START

from app.workflow.routes import (
    ANALYSIS,
    APPROVE,
    CLAIM,
    DEFINE_ACCEPTANCE,
    DELIVER,
    FINALIZE,
    LOCATE,
    PREPARE_CONTEXT,
    PUBLISH,
    REPAIR,
    REPRODUCE,
    RETRY,
    REVIEW,
    RUN_PREPARE,
    after_approval,
    after_claim,
    after_locate,
    after_prepare_context,
    after_repair,
    after_retry,
    after_review,
)
from app.workflow.spec import Dependency
from app.workflow.state import WorkflowState
from app.workflow.validation import DECIDE, PLAN, SUMMARY
from config.settings import settings

# 编排节点 -> 实际执行的插件。每个插件仍独立写入自己的步骤记录。
PHASE_COMPOSITIONS = {
    RUN_PREPARE: ("discover_logs", "normalize_error", "deduplicate_error"),
    PREPARE_CONTEXT: (
        "repair_branch_preflight",
        "prepare_source_snapshot",
        "prepare_workspace",
        "prepare_evidence",
    ),
}

REVIEW_EVIDENCE_STEPS = (
    "check_changes", "syntax_regression", PLAN, "verify_issue", "run_regression", SUMMARY,
)

RUN_PHASES = (RUN_PREPARE,)
TASK_PHASES = {
    PREPARE_CONTEXT: PHASE_COMPOSITIONS[PREPARE_CONTEXT],
}

# 业务编排节点；retry_prepare 是仅供有界回修使用的内部控制节点。
TASK_NODE_NAMES = (
    PREPARE_CONTEXT,
    LOCATE,
    DEFINE_ACCEPTANCE,
    REPRODUCE,
    CLAIM,
    REPAIR,
    REVIEW,
    DECIDE,
    DELIVER,
    PUBLISH,
    APPROVE,
    ANALYSIS,
    FINALIZE,
    RETRY,
)

TASK_STATIC_TRANSITIONS: tuple[tuple[str, str], ...] = ((START, PREPARE_CONTEXT),)


def cancelled(state: WorkflowState) -> bool:
    return str(state.get("terminal_status") or "").upper() == "CANCELLED"


def route_baseline(state: WorkflowState) -> str:
    if cancelled(state):
        return FINALIZE
    if state.get("last_result") == "passed":
        return CLAIM
    counts = (state.get("validation_recovery_counts") or {}).get(
        REPRODUCE, 0
    )
    if (
        state.get("retry_target") == REPRODUCE
        and counts < int(state.get("max_attempts") or settings.default_max_attempts) - 1
    ):
        return RETRY
    # 基线未建立（如缺少录制响应）不等于流程无法继续：交付根因分析和
    # 建议验收标准，由人工修复并验证，而不是把任务记成验证受阻。
    baseline = state.get("baseline_reproduction") or {}
    if (
        str(baseline.get("status") or "").lower() == "unverified"
        and state.get("locate")
        and state.get("ai_workspace_path")
    ):
        return ANALYSIS
    return FINALIZE


def route_locate(state: WorkflowState) -> str:
    result = after_locate(state)
    if result in {CLAIM, REPAIR}:
        return DEFINE_ACCEPTANCE
    return analysis_or_finalize(state) if result == FINALIZE else result


def analysis_or_finalize(state: WorkflowState) -> str:
    from app.workflow.routes import _analysis_only_task

    return (
        ANALYSIS
        if not cancelled(state)
        and state.get("ai_workspace_path")
        and _analysis_only_task(state)
        else FINALIZE
    )


def route_repair(state: WorkflowState) -> str:
    result = after_repair(state)
    return analysis_or_finalize(state) if result == FINALIZE else result


def route_candidate(state: WorkflowState) -> str:
    from app.workflow.routes import _analysis_only_task

    if cancelled(state):
        return FINALIZE
    if state.get("last_result") != "passed":
        from app.workflow.routes import _retry_or_finalize

        return (
            _retry_or_finalize(state)
            if state.get("retry_target") == DELIVER
            else FINALIZE
        )
    # 本地候选准备好后直接正式交付；人工审批只决定保留还是打回交付结果。
    return FINALIZE if _analysis_only_task(state) else PUBLISH


def route_approval(state: WorkflowState) -> str:
    # 旧顺序（审批→交付）挂起的任务恢复后还没有交付记录，先补交付再收尾。
    if (
        not cancelled(state)
        and state.get("approval_granted")
        and not state.get("approval_cleanup_failed")
        and not _delivery_landed(state)
    ):
        return PUBLISH
    return after_approval(state)


def route_publish(state: WorkflowState) -> str:
    # 交付成功才进入人工复核：复核只决定保留已交付分支还是打回撤回。
    if (
        not cancelled(state)
        and state.get("last_result") == "passed"
        and _delivery_landed(state)
    ):
        return APPROVE
    if (
        not cancelled(state)
        and state.get("last_result") == "failed"
        and state.get("retry_target") == PUBLISH
    ):
        attempts = (state.get("validation_recovery_counts") or {}).get(
            PUBLISH, 0
        )
        if attempts < int(state.get("max_attempts") or settings.default_max_attempts) - 1:
            return RETRY
    return FINALIZE


def _delivery_landed(state: WorkflowState) -> bool:
    """交付步骤确实产出了落到远端或源仓库本地分支的提交。"""
    delivery = state.get("delivery")
    if not isinstance(delivery, dict):
        return False
    commit = str(delivery.get("commit") or "").strip()
    return bool(commit) and bool(delivery.get("pushed") or delivery.get("local_branch"))


TASK_CONDITIONAL_TRANSITIONS = (
    (PREPARE_CONTEXT, after_prepare_context, (LOCATE, RETRY, FINALIZE)),
    # 定位不确定或非 carrier 范围结论只产出报告，不进入审批。
    (LOCATE, route_locate, (DEFINE_ACCEPTANCE, ANALYSIS, RETRY, FINALIZE)),
    (
        DEFINE_ACCEPTANCE,
        lambda s: (
            REPRODUCE if s.get("last_result") == "passed" else FINALIZE
        ),
        (REPRODUCE, FINALIZE),
    ),
    (REPRODUCE, route_baseline, (CLAIM, RETRY, ANALYSIS, FINALIZE)),
    (CLAIM, after_claim, (REPAIR, RETRY, FINALIZE)),
    (REPAIR, route_repair, (REVIEW, RETRY, FINALIZE, ANALYSIS)),
    (REVIEW, after_review, (DECIDE, FINALIZE)),
    (
        DECIDE,
        lambda state: (
            RETRY
            if (state.get("validation_decision") or {}).get("action")
            in {"retry", "repair"}
            else DELIVER
            if (state.get("validation_decision") or {}).get("action") == "deliver"
            else FINALIZE
        ),
        (RETRY, DELIVER, FINALIZE),
    ),
    # 人工复核在交付之后：通过即保留已交付分支，打回则撤回分支并在上限内回修。
    (APPROVE, route_approval, (RETRY, FINALIZE, PUBLISH)),
    (
        ANALYSIS,
        lambda s: DELIVER if s.get("last_result") == "passed" else FINALIZE,
        (DELIVER, FINALIZE),
    ),
    (DELIVER, route_candidate, (PUBLISH, RETRY, FINALIZE)),
    (PUBLISH, route_publish, (APPROVE, RETRY, FINALIZE)),
    (FINALIZE, lambda s: END, (END,)),
    (
        RETRY,
        after_retry,
        (
            PREPARE_CONTEXT,
            CLAIM,
            LOCATE,
            REPAIR,
            REVIEW,
            DELIVER,
            PUBLISH,
            REPRODUCE,
            FINALIZE,
        ),
    ),
)

__all__ = [
    "PHASE_COMPOSITIONS",
    "RUN_PHASES",
    "TASK_CONDITIONAL_TRANSITIONS",
    "TASK_NODE_NAMES",
    "TASK_PHASES",
    "TASK_STATIC_TRANSITIONS",
]


STEP_DEPENDENCIES = {
    "claim_repair_slot": [(REPRODUCE, "task")],
    "decide_validation": [(REVIEW, "task")],
    "deduplicate_error": [("normalize_error", "run")],
    DEFINE_ACCEPTANCE: [("locate_agent", "task")],
    "discover_logs": [],
    "finalize_execution": [],
    "identify_carrier": [("deduplicate_error", "run")],
    "normalize_error": [("discover_logs", "run")],
    ANALYSIS: [],
    "prepare_delivery": [],
    "prepare_evidence": [
        ("identify_carrier", "run"),
        ("prepare_source_snapshot", "task"),
        ("prepare_workspace", "task"),
    ],
    "prepare_source_snapshot": [("repair_branch_preflight", "task")],
    "prepare_workspace": [
        ("identify_carrier", "run"),
        ("prepare_source_snapshot", "task"),
    ],
    PUBLISH: [("request_approval", "task")],
    "repair_branch_preflight": [("identify_carrier", "run")],
    REPRODUCE: [(DEFINE_ACCEPTANCE, "task")],
    "request_approval": [("prepare_delivery", "task")],
    "locate_agent": [
        ("prepare_source_snapshot", "task"),
        ("prepare_evidence", "task"),
        ("prepare_workspace", "task"),
    ],
    "repair_agent": [("claim_repair_slot", "task"), (REPRODUCE, "task")],
    "review_agent": [("repair_agent", "task")],
}


def dependencies_for(name: str, review_mode: str = "agent") -> tuple[Dependency, ...]:
    """完整执行、单步执行和恢复操作共用的当前步骤依赖。"""
    from app.workflow.spec import Dependency

    return tuple(
        Dependency(step, level) for step, level in STEP_DEPENDENCIES.get(name, ())
    )


def next_step_for(name: str, state: dict[str, Any]) -> str | None:
    """从权威图计算下一步；组合步骤只在组合内部顺序推进。"""
    for phase, members in PHASE_COMPOSITIONS.items():
        if name in members:
            index = members.index(name)
            if index + 1 < len(members):
                return members[index + 1]
            name = phase
            break
    for source, router, targets in TASK_CONDITIONAL_TRANSITIONS:
        if source == name:
            return router(state)
    return {
        "sync_source_repo": RUN_PREPARE,
        RUN_PREPARE: "identify_carrier",
        "identify_carrier": "task_dispatch",
    }.get(name)
