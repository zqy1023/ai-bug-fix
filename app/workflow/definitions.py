"""权威工作流定义。

图层只保留业务阶段、Agent、安全门禁、审批与交付节点。
组合阶段内部仍调用原始步骤插件，因此步骤级调试、审计和产物记录不变。
"""
from __future__ import annotations

from app.workflow.routes import (
    APPLY, APPROVE, CLAIM, DELIVER, FINALIZE, LOCATE, PREPARE_CONTEXT,
    REGRESSION, REPAIR, REVIEW, RETRY, RUN_PREPARE, VALIDATE, WORKSPACE,
    after_apply, after_approval, after_claim, after_prepare_context,
    after_regression, after_repair, after_review, after_run_prepare,
    after_validate_changes, after_workspace, after_locate,
)
from langgraph.graph import END, START

# 编排节点 -> 实际执行的插件。每个插件仍独立写入自己的步骤记录。
PHASE_COMPOSITIONS = {
    RUN_PREPARE: ("discover_logs", "normalize_error", "deduplicate_error"),
    PREPARE_CONTEXT: ("prepare_source_snapshot", "prepare_evidence"),
    VALIDATE: ("check_changes", "verify_issue"),
}

RUN_PHASES = (RUN_PREPARE,)
TASK_PHASES = {
    PREPARE_CONTEXT: PHASE_COMPOSITIONS[PREPARE_CONTEXT],
    VALIDATE: PHASE_COMPOSITIONS[VALIDATE],
}

# 12 个业务主节点；retry_prepare 是仅供有界回修使用的内部控制节点。
TASK_NODE_NAMES = (
    PREPARE_CONTEXT, LOCATE, CLAIM, WORKSPACE, REPAIR, VALIDATE,
    REGRESSION, REVIEW, APPROVE, APPLY, DELIVER, FINALIZE, RETRY,
)

TASK_STATIC_TRANSITIONS: tuple[tuple[str, str], ...] = (
    (START, PREPARE_CONTEXT),
    (RETRY, REPAIR),
    (DELIVER, FINALIZE),
    (FINALIZE, END),
)

TASK_CONDITIONAL_TRANSITIONS = (
    (PREPARE_CONTEXT, after_prepare_context, (LOCATE, FINALIZE)),
    # after_locate may route to APPROVE when manual intervention is required.
    (LOCATE, after_locate, (CLAIM, APPROVE, FINALIZE)),
    (CLAIM, after_claim, (WORKSPACE, FINALIZE)),
    (WORKSPACE, after_workspace, (REPAIR, FINALIZE)),
    (REPAIR, after_repair, (VALIDATE, RETRY, APPROVE, FINALIZE)),
    (VALIDATE, after_validate_changes, (REGRESSION, RETRY, FINALIZE)),
    (REGRESSION, after_regression, (REVIEW, APPROVE, RETRY, FINALIZE)),
    (REVIEW, after_review, (REPAIR, APPROVE, DELIVER, RETRY, FINALIZE)),
    # Manual-intervention approval can resume at LOCATE or CLAIM; normal approval uses the other targets.
    (APPROVE, after_approval, (LOCATE, CLAIM, REPAIR, APPLY, DELIVER, RETRY, FINALIZE)),
    (APPLY, after_apply, (VALIDATE, RETRY, FINALIZE)),
)

__all__ = [
    "PHASE_COMPOSITIONS", "RUN_PHASES", "TASK_CONDITIONAL_TRANSITIONS",
    "TASK_NODE_NAMES", "TASK_PHASES", "TASK_STATIC_TRANSITIONS",
]
