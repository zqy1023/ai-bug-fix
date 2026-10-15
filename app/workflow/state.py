"""主图与任务子图的状态定义。

一次执行先在 run 级步骤里产出任务清单，再按 ``tasks`` 顺序逐任务串行执行任务子图。

任务局部通道（``changed_files`` / ``repair`` / ``locate`` / ``evidence`` 等）只存在于
任务子图的 :class:`WorkflowState` 里，不进入父图 :class:`RunState`。这样切分有两个
必须的理由：

1. 任务之间不能互相污染。``changed_files`` 在单个任务内要用 ``_union`` 合并
   "修复 Agent 的改动"与"获批共享层落盘的改动"，但跨任务必须归零；若放在父图，
   第二个任务会继承第一个任务的改动清单，门禁和交付都会算错。
2. 交付是按任务做的。每个任务在自己的子图里走完交付，父图只汇总结论。
"""

from __future__ import annotations

import operator
from typing import Annotated, Any

from typing_extensions import TypedDict


def _merge(current: dict[str, Any] | None, update: dict[str, Any] | None) -> dict[str, Any]:
    """字典通道按键覆盖。"""
    merged = dict(current or {})
    merged.update(update or {})
    return merged


def _union(current: list[str] | None, update: list[str] | None) -> list[str]:
    """路径清单按出现顺序去重合并，后写入的清单不能抹掉先写入的。"""
    seen: dict[str, None] = {}
    for item in [*(current or []), *(update or [])]:
        text = str(item).replace("\\", "/").strip()
        if text:
            seen.setdefault(text, None)
    return list(seen)


class WorkflowState(TypedDict, total=False):
    """任务子图状态：单个 carrier 任务从源码快照到交付的完整通道。"""

    # 身份与执行参数：由父图分发任务时注入
    run_id: str
    task_id: str
    task: dict[str, Any]
    problem: dict[str, Any]
    source_repo_path: str
    attempt: int
    max_attempts: int
    review_mode: str
    retry_reason: str

    # 任务局部产物
    source_project_path: str
    framework: str
    ai_workspace_path: str
    baseline_manifest: Annotated[dict[str, str], _merge]
    baseline_dir: str
    changed_files: Annotated[list[str], _union]
    # 两类改动分开记账：repair_changed_files 是修复 Agent 在 carrier 内的改动，
    # plan_changed_files 是获批共享层方案实际落盘的文件。changed_files 始终是
    # 两者去重后的并集，供门禁、回归和交付使用；任一类落盘都不能覆盖另一类。
    repair_changed_files: list[str]
    plan_changed_files: list[str]
    repair: dict[str, Any]
    locate: dict[str, Any]
    review: dict[str, Any]
    evidence: dict[str, Any]
    check_passed: bool
    verify_passed: bool
    regression_passed: bool
    decision: str
    last_result: str
    next_step: str
    gate_result: dict[str, Any]
    verify_result: dict[str, Any]
    regression_result: dict[str, Any]
    terminal_status: str
    # apply_plan 通道：方案契约、人工授权后的放行范围与落盘结果。
    change_plan: dict[str, Any]
    blocked_reason: str
    scope_escalation_required: bool
    escalation_used: bool
    authorized_files: list[str]
    plan_applied: bool
    # 审查结论与审批状态，交付门禁只认这两个字段，不从步骤记录里回读。
    review_decision: str
    approval_granted: bool
    # 收尾摘要：finalize_execution 写入，父图收集后累加进 task_results。
    task_summary: dict[str, Any]


class RunState(TypedDict, total=False):
    """父图状态：run 级步骤、任务分发游标与各任务结果汇总。

    ``task`` / ``problem`` / ``terminal_status`` / ``task_summary`` 用普通通道
    （后写覆盖）而不是 ``_merge``：分发节点每个任务都要把它们重置成本任务的值，
    合并语义会让上一个任务的值残留下来。
    """

    schema_version: int
    run_id: str
    source_repo_path: str
    log_service_name: str
    log_service_prefix: str
    log_excluded_messages: list[str]
    log_excluded_biztypes: list[str]
    log_days: int
    log_limit: int
    attempt: int
    max_attempts: int
    review_mode: str
    tasks: list[dict[str, Any]]
    task_cursor: int
    task: dict[str, Any]
    task_id: str
    problem: dict[str, Any]
    terminal_status: str
    task_summary: dict[str, Any]
    task_results: Annotated[list[dict[str, Any]], operator.add]
    last_result: str
    next_step: str


__all__ = ["RunState", "WorkflowState"]
