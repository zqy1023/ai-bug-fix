"""权威工作流定义。

图层只保留业务阶段、Agent、安全门禁、审批与交付节点。
组合阶段内部仍调用原始步骤插件，因此步骤级调试、审计和产物记录不变。
"""
from __future__ import annotations

from app.workflow.routes import (
    APPROVE, CLAIM, DELIVER, FINALIZE, LOCATE, PREPARE_CONTEXT,
    REGRESSION, REPAIR, REVIEW, RETRY, RUN_PREPARE, VALIDATE,
    after_approval, after_delivery, after_finalize, after_retry, after_claim, after_prepare_context,
    after_regression, after_repair, after_review, after_validate_changes, after_locate,
)
from langgraph.graph import END, START

# 编排节点 -> 实际执行的插件。每个插件仍独立写入自己的步骤记录。
PHASE_COMPOSITIONS = {
    RUN_PREPARE: ("discover_logs", "normalize_error", "deduplicate_error"),
    PREPARE_CONTEXT: ("prepare_source_snapshot", "repair_branch_preflight", "prepare_workspace", "prepare_evidence"),
    VALIDATE: ("check_changes", "syntax_regression", "verify_issue"),
}

RUN_PHASES = (RUN_PREPARE,)
TASK_PHASES = {
    PREPARE_CONTEXT: PHASE_COMPOSITIONS[PREPARE_CONTEXT],
    VALIDATE: PHASE_COMPOSITIONS[VALIDATE],
}

# 10 个业务主节点；retry_prepare 是仅供有界回修使用的内部控制节点。
TASK_NODE_NAMES = (
    PREPARE_CONTEXT, LOCATE, CLAIM, REPAIR, VALIDATE,
    REGRESSION, REVIEW, DELIVER, APPROVE, FINALIZE, RETRY,
)

TASK_STATIC_TRANSITIONS: tuple[tuple[str, str], ...] = (
    (START, PREPARE_CONTEXT),
)

TASK_CONDITIONAL_TRANSITIONS = (
    (PREPARE_CONTEXT, after_prepare_context, (LOCATE, RETRY, FINALIZE)),
    # 定位不确定或非 carrier 范围结论只产出报告，不进入审批。
    (LOCATE, after_locate, (CLAIM, RETRY, FINALIZE)),
    (CLAIM, after_claim, (REPAIR, RETRY, FINALIZE)),
    (REPAIR, after_repair, (VALIDATE, RETRY, FINALIZE)),
    (VALIDATE, after_validate_changes, (REGRESSION, RETRY, FINALIZE)),
    # human 模式跳过 AI 审查直接交付分支，因此 REGRESSION 也可以直达 DELIVER。
    (REGRESSION, after_regression, (REVIEW, DELIVER, RETRY, FINALIZE)),
    (REVIEW, after_review, (DELIVER, RETRY, FINALIZE)),
    # APPROVE 仅用于分支交付后的最终人工审批。
    (APPROVE, after_approval, (LOCATE, CLAIM, REPAIR, RETRY, FINALIZE)),
    (DELIVER, after_delivery, (APPROVE, RETRY, FINALIZE, END)),
    (FINALIZE, after_finalize, (DELIVER, END)),
    (RETRY, after_retry, (PREPARE_CONTEXT, CLAIM, LOCATE, REPAIR, VALIDATE, REGRESSION, REVIEW, DELIVER, FINALIZE)),
)

__all__ = [
    "PHASE_COMPOSITIONS", "RUN_PHASES", "TASK_CONDITIONAL_TRANSITIONS",
    "TASK_NODE_NAMES", "TASK_PHASES", "TASK_STATIC_TRANSITIONS",
]
