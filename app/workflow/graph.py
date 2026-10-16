"""主图与任务子图。

run 级主图负责拉日志、规范化、去重、识别运输商，然后把任务清单交给串行分发；
任务子图负责单个任务的完整修复链路。

分发节点每次只放行一个任务，任务走到 ``finalize_execution`` 并记完汇总之后才推进
游标，所以任务之间严格串行，不存在并发执行。任务子图自带任务局部状态，父图只留
run 级字段与结果汇总。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.workflow.state import RunState, WorkflowState
from app.workflow.definitions import TASK_CONDITIONAL_TRANSITIONS, TASK_NODE_NAMES, TASK_STATIC_TRANSITIONS
from app.workflow.routes import (
    RUN_PREPARE,
    after_run_prepare,
    APPROVE,
    CHECK,
    CLAIM,
    DEDUPLICATE,
    DELIVER,
    DISCOVER,
    EVIDENCE,
    FINALIZE,
    IDENTIFY,
    LOCATE,
    NORMALIZE,
    REGRESSION,
    REPAIR,
    REVIEW,
    RETRY,
    SNAPSHOT,
    VERIFY,
    WORKSPACE,
    after_approval,
    after_delivery,
    after_finalize,
    after_retry,
    after_check_changes,
    after_claim,
    after_evidence,
    after_identify,
    after_locate,
    after_regression,
    after_repair,
    after_review,
    after_source_snapshot,
    after_verify,
    after_workspace,
)
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy
from config.settings import settings

# 父图内部节点：串行分发一个任务、执行任务子图、收集该任务结果。
RUN_TASK = "run_task"
TASK_DISPATCH = "task_dispatch"
TASK_COLLECT = "task_collect"

_RUN_NODES = (RUN_PREPARE, IDENTIFY)



def _transient_retry() -> RetryPolicy:
    """瞬时错误重试策略。"""
    return RetryPolicy(
        max_attempts=settings.graph_retry_attempts,
        retry_on=(TimeoutError, ConnectionError, OSError),
    )


def build_task_graph(nodes: dict[str, Callable[..., Any]]) -> CompiledStateGraph:
    """编译任务子图。

    不挂 checkpointer：它作为节点嵌套进父图，持久化由父图的 checkpointer 统一接管。
    单独挂一份会让人工审批的 interrupt 落到两个不同的检查点上。
    """
    graph: StateGraph = StateGraph(WorkflowState)
    # 任务图含文件写入、审批和 Git 交付等副作用节点，不做通用自动重试。
    # 业务重试通过 retry_prepare 统一计数，避免重复提交或推送。
    for name in TASK_NODE_NAMES:
        if name == RETRY:
            def retry_node(state: dict[str, Any]) -> dict[str, Any]:
                # Agent/审批重试前恢复不可变基线，避免在上一次失败的半成品上继续修改。
                workspace_manager = nodes.get("_workspace_manager")
                if workspace_manager is not None:
                    try:
                        task = dict(state.get("task") or {})
                        workspace_manager.reset_task_workspace(
                            state.get("ai_workspace_path") or "",
                            state.get("source_repo_path") or "",
                            state.get("baseline_dir") or "",
                            state.get("source_project_path") or "",
                            str(task.get("carrier") or ""),
                        )
                    except Exception as error:
                        return {
                            "attempt": int(state.get("attempt") or 1),
                            "last_result": "failed",
                            "terminal_status": "failed",
                            "retry_target": "__retry_reset_failed__",
                            "retry_reason": f"重试前恢复隔离工作区失败：{error}",
                        }
                return {
                    "attempt": int(state.get("attempt") or 1) + 1,
                    "decision": "PENDING",
                    "approval_granted": False,
                    "review_decision": "",
                    "locate": {},
                    "repair": {},
                    "review": {},
                    "evidence": {},
                    "changed_files": [],
                    "repair_changed_files": [],
                    "change_plan": {},
                    "scope_escalation_required": False,
                    "repair_slot_claimed": False,
                    "authorized_files": [],
                    "check_passed": False,
                    "verify_passed": False,
                    "regression_passed": False,
                    "gate_result": {},
                    "verify_result": {},
                    "regression_result": {},
                    "delivery": {},
                    "delivery_pushed": False,
                    "delivery_commit": "",
                    "repair_branch": "",
                    "approval_cleanup_failed": False,
                    "last_result": "passed",
                }
            graph.add_node(name, retry_node)
        else:
            graph.add_node(name, nodes[name])

    # 任务流程唯一由 definitions.py 声明；这里仅负责将定义编译成 LangGraph。
    for source, target in TASK_STATIC_TRANSITIONS:
        graph.add_edge(source, target)
    for source, router, targets in TASK_CONDITIONAL_TRANSITIONS:
        graph.add_conditional_edges(source, router, list(targets))

    return graph.compile()


def dispatch(state: RunState) -> dict[str, Any]:
    """把游标指向的任务铺进状态，并清空上一个任务的任务局部字段。

    任务局部字段都声明为普通通道，这里显式覆盖即可归零；``changed_files`` 之类
    带 ``_union`` 的通道不在这里，它们只活在任务子图内部。
    """
    tasks = list(state.get("tasks") or [])
    cursor = int(state.get("task_cursor") or 0)
    if cursor >= len(tasks):
        return {"last_result": "passed"}
    task = dict(tasks[cursor])
    return {
        "task_cursor": cursor,
        "task_id": str(task.get("task_id") or ""),
        "task": task,
        "problem": dict(task.get("problem") or {}),
        "terminal_status": "",
        "task_summary": {},
        "attempt": 1,
        "retry_reason": "",
        "last_result": "passed",
    }


def after_dispatch(state: RunState) -> str:
    """还有任务就继续跑下一个，跑完就收工。"""
    tasks = list(state.get("tasks") or [])
    return RUN_TASK if int(state.get("task_cursor") or 0) < len(tasks) else END


def collect(state: RunState) -> dict[str, Any]:
    """记下刚跑完的任务结论，并把游标推进到下一个任务。"""
    cursor = int(state.get("task_cursor") or 0)
    task = dict(state.get("task") or {})
    summary = dict(state.get("task_summary") or {})
    if not summary:
        summary = {
            "task_id": str(task.get("task_id") or ""),
            "carrier": str(task.get("carrier") or ""),
            "biztype": str(task.get("biztype") or ""),
            "result": str(state.get("terminal_status") or "FAILED"),
        }
    return {"task_cursor": cursor + 1, "task_results": [summary]}


def build_graph(
    nodes: dict[str, Callable[..., Any]],
    task_graph: CompiledStateGraph,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """编译 run 级主图。``task_graph`` 作为 ``run_task`` 节点嵌套进来。"""
    graph: StateGraph = StateGraph(RunState)
    # 只有日志拉取是无副作用的外部读取，允许瞬时故障自动重试。
    retry = _transient_retry()
    for name in _RUN_NODES:
        graph.add_node(name, nodes[name], retry_policy=retry if name == RUN_PREPARE else None)
    graph.add_node(TASK_DISPATCH, dispatch)
    graph.add_node(RUN_TASK, task_graph)
    graph.add_node(TASK_COLLECT, collect)

    graph.add_edge(START, RUN_PREPARE)
    graph.add_conditional_edges(RUN_PREPARE, after_run_prepare, [IDENTIFY, END])
    graph.add_conditional_edges(IDENTIFY, after_identify, [TASK_DISPATCH, END])
    graph.add_conditional_edges(TASK_DISPATCH, after_dispatch, [RUN_TASK, END])
    graph.add_edge(RUN_TASK, TASK_COLLECT)
    graph.add_edge(TASK_COLLECT, TASK_DISPATCH)

    return graph.compile(checkpointer=checkpointer)


__all__ = [
    "APPROVE",
    "CHECK",
    "CLAIM",
    "DEDUPLICATE",
    "DELIVER",
    "DISCOVER",
    "EVIDENCE",
    "FINALIZE",
    "IDENTIFY",
    "LOCATE",
    "NORMALIZE",
    "REGRESSION",
    "REPAIR",
    "REVIEW",
    "RETRY",
    "RUN_PREPARE",
    "RUN_TASK",
    "SNAPSHOT",
    "TASK_COLLECT",
    "TASK_DISPATCH",
    "VERIFY",
    "WORKSPACE",
    "after_approval",
    "after_delivery",
    "after_finalize",
    "after_retry",
    "after_check_changes",
    "after_claim",
    "after_deduplicate",
    "after_dispatch",
    "after_evidence",
    "after_identify",
    "after_locate",
    "after_regression",
    "after_repair",
    "after_review",
    "after_source_snapshot",
    "after_verify",
    "after_workspace",
    "build_graph",
    "build_task_graph",
    "collect",
    "dispatch",
]
