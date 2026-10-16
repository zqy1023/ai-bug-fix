"""权威任务流程定义。

任务节点的顺序、条件路由及合法目标集中声明于此处；graph.py 只负责编译，
避免流程拓扑散落在装配代码和文档中。路由决策仍使用类型安全的 Python 函数。
"""
from __future__ import annotations

from app.workflow.routes import (
    APPLY, APPROVE, CHECK, CLAIM, DELIVER, EVIDENCE, FINALIZE, LOCATE,
    REGRESSION, REPAIR, REVIEW, RETRY, SNAPSHOT, VERIFY, WORKSPACE,
    after_apply, after_approval, after_check_changes, after_claim,
    after_evidence, after_locate, after_regression, after_repair,
    after_review, after_source_snapshot, after_verify, after_workspace,
)
from langgraph.graph import END, START

TASK_STATIC_TRANSITIONS: tuple[tuple[str, str], ...] = (
    (START, SNAPSHOT),
    (RETRY, REPAIR),
    (DELIVER, FINALIZE),
    (FINALIZE, END),
)

TASK_CONDITIONAL_TRANSITIONS = (
    (SNAPSHOT, after_source_snapshot, (EVIDENCE, FINALIZE)),
    (EVIDENCE, after_evidence, (LOCATE, FINALIZE)),
    (LOCATE, after_locate, (CLAIM, FINALIZE)),
    (CLAIM, after_claim, (WORKSPACE, FINALIZE)),
    (WORKSPACE, after_workspace, (REPAIR, FINALIZE)),
    (REPAIR, after_repair, (CHECK, RETRY, APPROVE, FINALIZE)),
    (CHECK, after_check_changes, (VERIFY, RETRY, FINALIZE)),
    (VERIFY, after_verify, (REGRESSION, RETRY, FINALIZE)),
    (REGRESSION, after_regression, (REVIEW, APPROVE, RETRY, FINALIZE)),
    (REVIEW, after_review, (REPAIR, APPROVE, DELIVER, RETRY, FINALIZE)),
    (APPROVE, after_approval, (REPAIR, APPLY, DELIVER, RETRY, FINALIZE)),
    (APPLY, after_apply, (CHECK, RETRY, FINALIZE)),
)

__all__ = ["TASK_CONDITIONAL_TRANSITIONS", "TASK_STATIC_TRANSITIONS"]
