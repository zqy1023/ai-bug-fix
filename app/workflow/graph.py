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
    SOURCE_SYNC,
    after_source_sync,
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
    PREPARE_CONTEXT,
    REGRESSION,
    VALIDATE,
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
    after_claim,
    after_identify,
    after_locate,
    after_regression,
    after_repair,
    after_review,
)
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy
from config.settings import settings
from app.infrastructure.redaction import public_exception

# 父图内部节点：串行分发一个任务、执行任务子图、收集该任务结果。
RUN_TASK = "run_task"
TASK_DISPATCH = "task_dispatch"
TASK_COLLECT = "task_collect"

_RUN_NODES = (SOURCE_SYNC, RUN_PREPARE, IDENTIFY)



class _RetryableNodeFailure(RuntimeError):
    """A node returned a recoverable failure instead of raising an exception."""


def _retry_failed_result(name: str, node: Callable[..., Any]) -> Callable[..., Any]:
    """Make failed run-level result envelopes visible to LangGraph RetryPolicy."""
    def wrapped(state: dict[str, Any]) -> dict[str, Any]:
        result = node(state)
        if isinstance(result, dict) and str(result.get("last_result") or "").lower() == "failed":
            reason = str(result.get("retry_reason") or result.get("terminal_reason") or "节点返回失败结论")
            raise _RetryableNodeFailure(f"{name}: {reason[:1200]}")
        return result

    wrapped.__name__ = name
    return wrapped


def _transient_retry() -> RetryPolicy:
    """瞬时错误重试策略。"""
    from app.infrastructure.git.source import SourceRepoError

    # Settings.graph_retry_attempts means retries after the first attempt;
    # LangGraph's max_attempts counts the first execution as well.
    retry_count = max(0, int(settings.graph_retry_attempts))
    return RetryPolicy(
        max_attempts=retry_count + 1,
        retry_on=(TimeoutError, ConnectionError, OSError, SourceRepoError, _RetryableNodeFailure),
    )


def build_task_graph(
    nodes: dict[str, Callable[..., Any]],
    *,
    workspace_manager: Any | None = None,
) -> CompiledStateGraph:
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
                # 审查重试是复查同一份改动，重置工作区会删掉待审内容，必须跳过。
                target = str(state.get("retry_target") or "")
                manager = workspace_manager
                # 这些重试阶段要继续审查/验证已修改的工作区，不能先恢复成基线。
                preserve_workspace_targets = {PREPARE_CONTEXT, CLAIM, VALIDATE, REGRESSION, REVIEW, DELIVER}
                preserve_workspace = target in preserve_workspace_targets
                if not preserve_workspace and manager is None:
                    return {
                        "attempt": int(state.get("attempt") or 1),
                        "last_result": "failed",
                        "terminal_status": "failed",
                        "retry_target": "__retry_reset_failed__",
                        "retry_reason": "重试前未配置工作区管理器，无法安全恢复代码基线",
                    }
                if not preserve_workspace:
                    try:
                        task = dict(state.get("task") or {})
                        manager.reset_task_workspace(
                            state.get("ai_workspace_path") or "",
                            state.get("baseline_dir") or "",
                            str(task.get("carrier") or ""),
                        )
                        manifest = {
                            str(key): str(value)
                            for key, value in (state.get("baseline_manifest") or {}).items()
                        }
                        if manifest:
                            valid, mismatches = manager.verify_manifest(
                                state.get("ai_workspace_path") or "",
                                manifest,
                                carrier=str(task.get("carrier") or "") or None,
                            )
                            if not valid:
                                return {
                                    "attempt": int(state.get("attempt") or 1),
                                    "last_result": "failed",
                                    "terminal_status": "failed",
                                    "retry_target": "__retry_reset_failed__",
                                    "retry_reason": "重试后工作区与基线不一致",
                                    "blocked_reason": f"基线校验失败：{', '.join(mismatches[:8])}",
                                }
                    except Exception as error:
                        return {
                            "attempt": int(state.get("attempt") or 1),
                            "last_result": "failed",
                            "terminal_status": "failed",
                            "retry_target": "__retry_reset_failed__",
                            "retry_reason": public_exception(error, prefix="重试前恢复隔离工作区失败"),
                        }
                patch: dict[str, Any] = {
                    "attempt": int(state.get("attempt") or 1) + 1,
                    "automatic_attempt": int(state.get("automatic_attempt") or state.get("attempt") or 1) + 1,
                    "decision": "PENDING",
                    "approval_granted": False,
                    "review_decision": str(state.get("review_decision") or "") if target == DELIVER else "",
                    "review": dict(state.get("review") or {}) if target == DELIVER else {},
                    "delivery": {},
                    "delivery_pushed": False,
                    "delivery_local_branch": False,
                    "delivery_commit": "",
                    "repair_branch": "",
                    "approval_cleanup_failed": False,
                    "approval_recovery_blocked": False,
                    "blocked_reason": "",
                    "repair_slot_claimed": kept_repair_slot(state),
                    "last_result": "passed",
                }
                if target not in {VALIDATE, REGRESSION, REVIEW, DELIVER}:
                    # 定位/修复重跑从干净基线开始，避免在上次半成品上叠加修改。
                    patch.update({
                        "repair": {},
                        "changed_files": [],
                        "repair_changed_files": [],
                        "change_plan": {},
                        "scope_escalation_required": False,
                        "check_passed": False,
                        "verify_passed": False,
                        "regression_passed": False,
                        "gate_result": {},
                        "verify_result": {},
                        "regression_result": {},
                    })
                elif target == VALIDATE:
                    # 重跑变更门禁和问题验证，保留源码，但清空这一阶段及下游的旧结论。
                    patch.update({
                        "check_passed": False,
                        "verify_passed": False,
                        "regression_passed": False,
                        "gate_result": {},
                        "verify_result": {},
                        "regression_result": {},
                    })
                elif target == REGRESSION:
                    # 只重跑回归时保留已有门禁与问题验证结论。
                    patch.update({
                        "regression_passed": False,
                        "regression_result": {},
                    })
                if target == PREPARE_CONTEXT:
                    # 重新准备必须丢弃上一轮可能不完整的基线、证据和工作区指针。
                    patch.update({
                        "baseline_manifest": {},
                        "baseline_dir": "",
                        "source_project_path": "",
                        "ai_workspace_path": "",
                        "evidence": {},
                        "framework": "",
                        "locate": {},
                    })
                if target == LOCATE:
                    # 重试定位要清掉旧结论；重试修复必须保留 LOCATED 结论和
                    # 确定性 evidence，否则 repair_agent 输入校验会直接失败。
                    patch["locate"] = {}
                return patch
            graph.add_node(name, retry_node)
        else:
            graph.add_node(name, nodes[name])

    # 任务流程唯一由 definitions.py 声明；这里仅负责将定义编译成 LangGraph。
    for source, target in TASK_STATIC_TRANSITIONS:
        graph.add_edge(source, target)
    for source, router, targets in TASK_CONDITIONAL_TRANSITIONS:
        graph.add_conditional_edges(source, router, list(targets))

    return graph.compile()


def kept_repair_slot(state: dict[str, Any]) -> bool:
    """同一任务回修继续使用已领取资格，不在重试时伪造未领取。"""
    return bool(state.get("repair_slot_claimed"))


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
    # A manual task re-run reuses the original task input but increments its lifetime
    # attempt. Automatic retry budget starts from one for this new execution cycle.
    task_attempt = int(task.pop("_workflow_attempt", 1) or 1)
    return {
        "task_cursor": cursor,
        "task_id": str(task.get("task_id") or ""),
        "task": task,
        "problem": dict(task.get("problem") or {}),
        "terminal_status": "",
        "task_summary": {},
        "attempt": task_attempt,
        "automatic_attempt": 1,
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
    # 源仓库同步与日志发现/规范化允许有限的瞬时故障重试；任务图副作用节点不使用通用重试。
    retry = _transient_retry()
    for name in _RUN_NODES:
        handler = _retry_failed_result(name, nodes[name]) if name in {RUN_PREPARE, IDENTIFY} else nodes[name]
        graph.add_node(
            name,
            handler,
            retry_policy=retry if name in {SOURCE_SYNC, RUN_PREPARE, IDENTIFY} else None,
        )
    graph.add_node(TASK_DISPATCH, dispatch)
    graph.add_node(RUN_TASK, task_graph)
    graph.add_node(TASK_COLLECT, collect)

    graph.add_edge(START, SOURCE_SYNC)
    graph.add_conditional_edges(SOURCE_SYNC, after_source_sync, [RUN_PREPARE, END])
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
    "after_claim",
    "after_dispatch",
    "after_identify",
    "after_locate",
    "after_regression",
    "after_repair",
    "after_review",
    "build_graph",
    "build_task_graph",
    "collect",
    "dispatch",
    "kept_repair_slot",
]
