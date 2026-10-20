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
    # seen：该状态字段供工作流节点传递结构化执行数据。
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
    # task_id：当前任务标识。
    task_id: str
    # task：当前运输商任务的结构化输入。
    task: dict[str, Any]
    # problem：本任务的规范化异常问题。
    problem: dict[str, Any]
    # source_repo_path：源 Git 仓库路径。
    source_repo_path: str
    # attempt：当前修复尝试序号。
    attempt: int
    # max_attempts：单任务最大尝试次数。
    max_attempts: int
    # review_mode：审查模式。
    review_mode: str
    # allow_push：本轮交付是否允许推送远程分支。
    allow_push: bool
    # require_push：调用方是否要求必须存在远程并完成推送。
    require_push: bool
    # retry_reason：上一轮失败或驳回原因。
    retry_reason: str
    # retry_target：Agent 执行异常后重试的 Agent 名称；普通验证失败默认回到 repair_agent。
    retry_target: str

    # 任务局部产物
    source_project_path: str
    # framework：目标 carrier 的框架类型。
    framework: str
    # ai_workspace_path：隔离 AI 工作区路径。
    ai_workspace_path: str
    baseline_manifest: Annotated[dict[str, str], _merge]
    # baseline_dir：只读基线目录。
    baseline_dir: str
    # changed_files：当前尝试的实际变更文件；每次重试必须从空集合重新计算。
    changed_files: list[str]
    repair_changed_files: list[str]
    # repair：修复 Agent 的结构化结论。
    repair: dict[str, Any]
    # locate：根因定位 Agent 的结构化结论。
    locate: dict[str, Any]
    # review：审查 Agent 的结构化结论。
    review: dict[str, Any]
    # evidence：确定性预检与源码证据。
    evidence: dict[str, Any]
    # check_passed：变更安全门禁是否通过。
    check_passed: bool
    # verify_passed：问题修复验证是否通过。
    verify_passed: bool
    # regression_passed：准生产回归是否通过。
    regression_passed: bool
    # decision：当前审批决定。
    decision: str
    # last_result：最近节点的内部路由结论。
    last_result: str
    # next_step：下一节点标识。
    next_step: str
    # gate_result：逐文件门禁结果。
    gate_result: dict[str, Any]
    # verify_result：问题验证详情。
    verify_result: dict[str, Any]
    # regression_result：回归执行详情。
    regression_result: dict[str, Any]
    # terminal_status：任务收尾前的业务终态。
    terminal_status: str
    # change_plan：记录非 carrier 范围的分析结论，不进入自动落盘。
    change_plan: dict[str, Any]
    # blocked_reason：阻断或范围拒绝原因。
    blocked_reason: str
    # scope_escalation_required：是否需要共享层范围升级。
    scope_escalation_required: bool
    # repair_slot_claimed：本任务是否已经领取过修复资格，重新定位后不得重复领取。
    repair_slot_claimed: bool
    # 审查结论与审批状态，交付门禁只认这两个字段，不从步骤记录里回读。
    review_decision: str
    # approval_granted：人工审批是否通过。
    approval_granted: bool
    # 最终交付与审批清理状态。
    delivery: dict[str, Any]
    delivery_pushed: bool
    # delivery_local_branch：未推送时修复提交已写回源仓库的本地分支。
    delivery_local_branch: bool
    delivery_commit: str
    repair_branch: str
    approval_cleanup_failed: bool
    # 审批驳回但分支清理/回退目标不安全时禁止进入回修路由。
    approval_recovery_blocked: bool
    # 收尾摘要：finalize_execution 写入，父图收集后累加进 task_results。
    task_summary: dict[str, Any]


class RunState(TypedDict, total=False):
    """父图状态：run 级步骤、任务分发游标与各任务结果汇总。

    ``task`` / ``problem`` / ``terminal_status`` / ``task_summary`` 用普通通道
    （后写覆盖）而不是 ``_merge``：分发节点每个任务都要把它们重置成本任务的值，
    合并语义会让上一个任务的值残留下来。
    """

    # schema_version：状态结构版本号。
    schema_version: int
    # run_id：当前工作流执行标识。
    run_id: str
    # source_repo_path：源 Git 仓库路径。
    source_repo_path: str
    # log_service_name：ES 日志精确服务名称。
    log_service_name: str
    # log_service_prefix：ES 日志服务名前缀。
    log_service_prefix: str
    # log_excluded_messages：需要排除的日志错误消息。
    log_excluded_messages: list[str]
    # log_excluded_biztypes：需要排除的业务类型。
    log_excluded_biztypes: list[str]
    # log_days：日志回溯天数。
    log_days: int
    # log_limit：日志查询条数上限。
    log_limit: int
    # attempt：当前修复尝试序号。
    attempt: int
    # max_attempts：单任务最大尝试次数。
    max_attempts: int
    # review_mode：审查模式。
    review_mode: str
    # allow_push：本轮交付是否允许推送远程分支。
    allow_push: bool
    # require_push：调用方是否要求必须存在远程并完成推送。
    require_push: bool
    # tasks：本轮串行处理的任务清单。
    tasks: list[dict[str, Any]]
    # task_cursor：当前任务在清单中的游标。
    task_cursor: int
    # task：当前运输商任务的结构化输入。
    task: dict[str, Any]
    # task_id：当前任务标识。
    task_id: str
    # problem：本任务的规范化异常问题。
    problem: dict[str, Any]
    # terminal_status：任务收尾前的业务终态。
    terminal_status: str
    # task_summary：收尾后写入父图的任务汇总。
    task_summary: dict[str, Any]
    task_results: Annotated[list[dict[str, Any]], operator.add]
    # last_result：最近节点的内部路由结论。
    last_result: str
    # next_step：下一节点标识。
    next_step: str


__all__ = ["RunState", "WorkflowState"]
