"""主图与任务子图。

run 级主图负责拉日志、规范化、去重、识别运输商，然后把任务清单交给串行分发；
任务子图负责单个任务的完整修复链路。

分发节点每次只放行一个任务，任务走到 ``finalize_execution`` 并记完汇总之后才推进
游标，所以任务之间严格串行，不存在并发执行。任务子图自带任务局部状态，父图只留
run 级字段与结果汇总。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy

from app.domain.errors import StepFailed
from app.domain.keys import safe_name
from app.infrastructure.redaction import public_exception
from app.workflow.definitions import (
    TASK_CONDITIONAL_TRANSITIONS,
    TASK_NODE_NAMES,
    TASK_STATIC_TRANSITIONS,
)
from app.workflow.routes import (
    CLAIM,
    DELIVER,
    IDENTIFY,
    LOCATE,
    PREPARE_CONTEXT,
    PUBLISH,
    REPAIR,
    REPRODUCE,
    RETRY,
    RETRY_RESET_FAILED,
    REVIEW,
    RUN_PREPARE,
    SOURCE_SYNC,
    after_identify,
    after_run_prepare,
    after_source_sync,
)
from app.workflow.state import RunState, WorkflowState
from config.settings import settings

# 父图内部节点：串行分发一个任务、执行任务子图、收集该任务结果。
RUN_TASK = "run_task"
TASK_DISPATCH = "task_dispatch"
TASK_COLLECT = "task_collect"

_RUN_NODES = (SOURCE_SYNC, RUN_PREPARE, IDENTIFY)

logger = logging.getLogger(__name__)


class _RetryableNodeFailure(RuntimeError):
    """A node returned a recoverable failure instead of raising an exception."""


def _retry_failed_result(name: str, node: Callable[..., Any]) -> Callable[..., Any]:
    """Make failed run-level result envelopes visible to LangGraph RetryPolicy."""

    def wrapped(state: dict[str, Any]) -> dict[str, Any]:
        result = node(state)
        if (
            isinstance(result, dict)
            and str(result.get("last_result") or "").lower() == "failed"
        ):
            reason = str(
                result.get("retry_reason")
                or result.get("terminal_reason")
                or "节点返回失败结论"
            )
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
        retry_on=(
            TimeoutError,
            ConnectionError,
            OSError,
            SourceRepoError,
            _RetryableNodeFailure,
        ),
    )


def build_task_graph(
    nodes: dict[str, Callable[..., Any]],
    *,
    workspace_manager: Any | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    transition_store: Any | None = None,
    retry_context_builder: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    entry_node: str | None = None,
    workspace_root: Path | None = None,
) -> CompiledStateGraph:
    """编译任务子图。

    独立任务执行器显式传入 checkpointer；旧式嵌套和单元测试可继承父图持久化。
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
                from app.workflow.retry_context import capture_retry_context

                try:
                    retry_context = (retry_context_builder or capture_retry_context)(
                        state
                    )
                    if retry_context.get("capture_errors"):
                        raise StepFailed(
                            "重试证据采集不完整："
                            + "; ".join(retry_context["capture_errors"])
                        )
                except Exception as error:
                    reason = public_exception(
                        error, prefix="保存上一轮重试证据失败，保留当前工作区"
                    )
                    return {
                        "last_result": "failed",
                        "terminal_status": "FAILED",
                        "retry_target": RETRY_RESET_FAILED,
                        "retry_reason": reason,
                        "blocked_reason": reason,
                    }
                manager = workspace_manager
                # 验证/回归/审查/交付重试要继续使用当前改动；准备阶段重试则必须
                # 归档旧基线和工作区，否则 prepare_source_snapshot / prepare_workspace 会撞目录。
                preserve_workspace_targets = {
                    CLAIM,
                                                                    REVIEW,
                    DELIVER,
                    PUBLISH,
                    REPRODUCE,
                }
                preserve_workspace = target in preserve_workspace_targets
                if target == PREPARE_CONTEXT:
                    if manager is None:
                        return {
                            "attempt": int(state.get("attempt") or 1),
                            "last_result": "failed",
                            "terminal_status": "failed",
                            "retry_target": RETRY_RESET_FAILED,
                            "retry_reason": "重试准备阶段前未配置工作区管理器，无法安全隔离旧目录",
                        }
                    archived: list[tuple[Path, Path]] = []
                    try:
                        run_id = str(state.get("run_id") or "")
                        task_id = str(state.get("task_id") or "")
                        if not run_id or not task_id:
                            raise RuntimeError(
                                "缺少 run_id/task_id，无法定位旧的任务基线"
                            )
                        root = (
                            Path(workspace_root).resolve()
                            if workspace_root is not None
                            else None
                        )
                        if root is None:
                            raise RuntimeError("工作区根目录未配置")
                        # 任务基线目录以 RecordStore 的唯一布局定义为准，不再在此复制路径规则。
                        if transition_store is not None and hasattr(
                            transition_store, "task_dir"
                        ):
                            task_root = Path(
                                transition_store.task_dir(
                                    str(state.get("run_id") or ""),
                                    str(state.get("task_id") or ""),
                                )
                            ).resolve()
                        else:
                            task_root = (
                                root.parent
                                / "runs"
                                / safe_name(str(state.get("run_id") or ""))
                                / "tasks"
                                / safe_name(str(state.get("task_id") or ""))
                            ).resolve()
                        baseline_value = str(
                            state.get("baseline_dir") or task_root / "source_snapshot"
                        )
                        baseline = Path(baseline_value).resolve()
                        if not baseline.is_relative_to(task_root):
                            raise RuntimeError("源码基线超出当前 Task 目录")
                        workspace_value = str(state.get("ai_workspace_path") or "")
                        if not workspace_value and manager is None:
                            raise RuntimeError("缺少工作区管理器，无法推导默认工作区路径")
                        workspace = (
                            Path(workspace_value).resolve()
                            if workspace_value
                            else (
                                root
                                / "ai"
                                / manager.safe_name(f"{run_id}-{task_id}")
                            ).resolve()
                        )
                        if not workspace.is_relative_to(workspace_root):
                            raise RuntimeError("隔离工作区超出配置的 workspace_root")
                        suffix = f".previous-attempt-{int(state.get('attempt') or 1) + 1}-{uuid.uuid4().hex[:8]}"
                        for original in (baseline, workspace):
                            if original.exists():
                                archive = original.with_name(original.name + suffix)
                                original.replace(archive)
                                archived.append((original, archive))
                        # Old directories have been archived, so this retry creates both from scratch.
                        preserve_workspace = True
                    except Exception as error:
                        for original, archive in reversed(archived):
                            try:
                                if archive.exists() and not original.exists():
                                    archive.replace(original)
                            except OSError as restore_error:
                                # 回滚失败不中断，但必须留下工作区/基线路径便于人工恢复。
                                logger.warning(
                                    "重试前恢复旧基线/工作区失败 workspace=%s baseline=%s "
                                    "源路径=%s 归档路径=%s: %s",
                                    workspace,
                                    baseline,
                                    original,
                                    archive,
                                    restore_error,
                                )
                        return {
                            "attempt": int(state.get("attempt") or 1),
                            "last_result": "failed",
                            "terminal_status": "failed",
                            "retry_target": RETRY_RESET_FAILED,
                            "retry_reason": public_exception(
                                error, prefix="重试准备阶段前归档旧基线/工作区失败"
                            ),
                        }

                if not preserve_workspace and manager is None:
                    return {
                        "attempt": int(state.get("attempt") or 1),
                        "last_result": "failed",
                        "terminal_status": "failed",
                        "retry_target": RETRY_RESET_FAILED,
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
                            for key, value in (
                                state.get("baseline_manifest") or {}
                            ).items()
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
                                    "retry_target": RETRY_RESET_FAILED,
                                    "retry_reason": "重试后工作区与基线不一致",
                                    "blocked_reason": f"基线校验失败：{', '.join(mismatches[:8])}",
                                }
                    except Exception as error:
                        return {
                            "attempt": int(state.get("attempt") or 1),
                            "last_result": "failed",
                            "terminal_status": "failed",
                            "retry_target": RETRY_RESET_FAILED,
                            "retry_reason": public_exception(
                                error, prefix="重试前恢复隔离工作区失败"
                            ),
                        }
                patch: dict[str, Any] = {
                    "retry_context": retry_context,
                    "attempt": int(state.get("attempt") or 1) + 1,
                    "automatic_attempt": int(state.get("automatic_attempt") or 1)
                    + (1 if target in {REPAIR, LOCATE} else 0),
                    "validation_recovery_counts": {
                        **dict(state.get("validation_recovery_counts") or {}),
                        target: int(
                            (state.get("validation_recovery_counts") or {}).get(
                                target, 0
                            )
                        )
                        + 1,
                    },
                    "validation_decision": state.get("validation_decision", {})
                    if target in {DELIVER, PUBLISH} else {},
                    "terminal_status": "passed",
                    "decision": "PENDING",
                    "review": dict(state.get("review") or {})
                    if target in {DELIVER, PUBLISH}
                    else {},
                    "delivery": {}
                    if target != PUBLISH
                    else state.get("delivery", {}),
                    "approval_result": state.get("approval_result", {})
                    if target == PUBLISH
                    else {},
                    "approval_granted": bool(state.get("approval_granted"))
                    if target == PUBLISH
                    else False,
                    "delivery_candidate": state.get("delivery_candidate", {})
                    if target in {PUBLISH, DELIVER}
                    else {},
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
                if target not in {
                                                                    REVIEW,
                    DELIVER,
                    PUBLISH,
                    REPRODUCE,
                }:
                    # 定位/修复重跑从干净基线开始，避免在上次半成品上叠加修改。
                    patch.update(
                        {
                            "repair": {},
                            "changed_files": [],
                            "repair_changed_files": [],
                            "change_plan": {},
                            "validation_plan": {},
                            "validation_summary": {},
                            "scope_escalation_required": False,
                            "syntax_result": {},
                            "gate_result": {},
                            "verify_result": {},
                            "regression_result": {},
                        }
                    )
                if target == PREPARE_CONTEXT:
                    # 重新准备必须丢弃上一轮可能不完整的基线、证据和工作区指针。
                    patch.update(
                        {
                            "baseline_manifest": {},
                            "baseline_dir": "",
                            "source_project_path": "",
                            "ai_workspace_path": "",
                            "evidence": {},
                            "framework": "",
                            "locate": {},
                        }
                    )
                if target in {LOCATE, PREPARE_CONTEXT}:
                    patch.update(acceptance_contract={}, baseline_reproduction={})
                if target == LOCATE:
                    # 重试定位要清掉旧结论；重试修复必须保留 LOCATED 结论和
                    # 确定性 evidence，否则 repair_agent 输入校验会直接失败。
                    patch["locate"] = {}
                if transition_store is not None:
                    from app.workflow.handoff import apply_step_result, outcome_of

                    return apply_step_result(
                        transition_store,
                        state,
                        RETRY,
                        patch,
                        outcome_of(
                            {"target": target, "reason": state.get("retry_reason", "")}
                        ),
                    )
                return patch

            graph.add_node(name, retry_node)
        else:
            graph.add_node(name, nodes[name])

    # 任务流程唯一由 definitions.py 声明；这里仅负责将定义编译成 LangGraph。
    for source, target in TASK_STATIC_TRANSITIONS:
        graph.add_edge(source, entry_node if source == START and entry_node else target)
    for source, router, targets in TASK_CONDITIONAL_TRANSITIONS:
        graph.add_conditional_edges(source, router, list(targets))

    return graph.compile(checkpointer=checkpointer)


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
        "workflow_version": 7,
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
    task_collector: Callable[..., Any] | None = None,
    task_runner: Callable[..., Any] | None = None,
) -> CompiledStateGraph:
    """编译 run 级主图。``task_graph`` 作为 ``run_task`` 节点嵌套进来。"""
    graph: StateGraph = StateGraph(RunState)
    # 源仓库同步与日志发现/规范化允许有限的瞬时故障重试；任务图副作用节点不使用通用重试。
    retry = _transient_retry()
    for name in _RUN_NODES:
        handler = (
            _retry_failed_result(name, nodes[name])
            if name in {RUN_PREPARE, IDENTIFY}
            else nodes[name]
        )
        graph.add_node(
            name,
            handler,
            retry_policy=retry
            if name in {SOURCE_SYNC, RUN_PREPARE, IDENTIFY}
            else None,
        )
    graph.add_node(TASK_DISPATCH, dispatch)
    graph.add_node(RUN_TASK, task_runner or task_graph)
    graph.add_node(TASK_COLLECT, task_collector or collect)

    graph.add_edge(START, SOURCE_SYNC)
    graph.add_conditional_edges(SOURCE_SYNC, after_source_sync, [RUN_PREPARE, END])
    graph.add_conditional_edges(RUN_PREPARE, after_run_prepare, [IDENTIFY, END])
    graph.add_conditional_edges(IDENTIFY, after_identify, [TASK_DISPATCH, END])
    graph.add_conditional_edges(TASK_DISPATCH, after_dispatch, [RUN_TASK, END])
    graph.add_edge(RUN_TASK, TASK_COLLECT)
    graph.add_edge(TASK_COLLECT, TASK_DISPATCH)

    return graph.compile(checkpointer=checkpointer)


__all__ = [
    "RUN_TASK", "TASK_COLLECT", "TASK_DISPATCH", "after_dispatch",
    "build_graph", "build_task_graph", "collect", "dispatch", "kept_repair_slot",
]
