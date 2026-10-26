"""图装配。唯一入口：``build_graph``。

步骤插件由 :mod:`app.workflow` 发现，本文件只声明编排组合和图结构。
三个 agent 在各自目录编译，主图只接收编译后的图。
"""

from __future__ import annotations

import atexit
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph.state import CompiledStateGraph

from app.application.audit import StepError, StepRecord
from app.application.services import WorkflowServices
from app.domain.errors import StepFailed
from app.domain.models import utc_now
from app.infrastructure.config import RuntimeConfig, load_config
from app.infrastructure.es.logs import ESLogAdapter
from app.infrastructure.filesystem.workspace import WorkspaceConfig, WorkspaceManager
from app.infrastructure.git.delivery import GitBranchDelivery, GitDeliveryConfig
from app.infrastructure.git.source import (
    SourceRepoCancelled,
    SourceRepoPaused,
    sync_source_repo,
)
from app.infrastructure.records.store import RecordStore
from app.infrastructure.redaction import public_exception
from app.infrastructure.run_control import cancellation_requested, pause_requested
from app.workflow import registry
from app.workflow.agent_adapter import build_agent_node as build_agent_node
from app.workflow.definitions import PHASE_COMPOSITIONS
from app.workflow.graph import build_graph as build_state_graph
from app.workflow.graph import build_task_graph, collect
from app.workflow.plugin_checks import verify_registry
from app.workflow.plugin_loader import load_all
from app.workflow.registry import PluginError
from app.workflow.retry_context import capture_retry_context
from app.workflow.routes import SOURCE_SYNC
from app.workflow.runner import bind
from app.workflow.validation_executor import independent_verify
from config.settings import settings

_CHECKPOINT_LOCK = threading.Lock()
_CHECKPOINT_CONNECTIONS: dict[str, sqlite3.Connection] = {}
_CHECKPOINT_SAVERS: dict[str, SqliteSaver] = {}


def close_checkpoints() -> None:
    """关闭进程内复用的 checkpoint SQLite 连接。"""
    with _CHECKPOINT_LOCK:
        connections = list(_CHECKPOINT_CONNECTIONS.values())
        _CHECKPOINT_CONNECTIONS.clear()
        _CHECKPOINT_SAVERS.clear()
    for connection in connections:
        try:
            connection.close()
        except sqlite3.Error:
            pass


atexit.register(close_checkpoints)


def _checkpoint_saver(runtime_dir: Any) -> SqliteSaver:
    path = str((runtime_dir / settings.workflow_checkpoint_db_name).resolve())
    with _CHECKPOINT_LOCK:
        connection = _CHECKPOINT_CONNECTIONS.get(path)
        if connection is None:
            runtime_dir.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                path,
                check_same_thread=False,
                timeout=settings.workflow_checkpoint_timeout_seconds,
            )
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                f"PRAGMA busy_timeout={settings.workflow_checkpoint_busy_timeout_ms}"
            )
            _CHECKPOINT_CONNECTIONS[path] = connection
        saver = _CHECKPOINT_SAVERS.get(path)
        if saver is None:
            saver = SqliteSaver(connection)
            saver.setup()
            _CHECKPOINT_SAVERS[path] = saver
        return saver


def build_services(
    config: RuntimeConfig | None = None,
    source_repo: Path | None = None,
) -> WorkflowServices:
    """按当前配置装配外部依赖。缺配置的字段保持空，对应步骤运行时失败。

    源仓库同步由执行入口完成；只读的图装配（checkpoint 查询等）不触发网络操作。
    """
    from app.infrastructure.config import create_es_client, index_days
    from app.infrastructure.git.source import git_auth_env
    from app.agents.review.tools.run_regression.request_runner import run_request_regression

    config = config or load_config()
    resolved_source = source_repo if source_repo is not None else config.source_repo
    workspaces = WorkspaceManager(
        WorkspaceConfig(
            root_dir=config.workspace_root, project_subdir=config.project_subdir
        )
    )
    log_source = None
    if config.log_es:
        from elasticsearch7.helpers import scan

        log_source = ESLogAdapter(
            create_es_client(config),
            scan,
            lambda prefix, days: index_days(prefix, days, config.log_tz_offset),
            index_prefix=config.es_index_prefix,
            scan_size=config.es_scan_size,
            scroll=config.es_scroll,
            excluded_messages=list(config.excluded_messages),
            request_timeout=config.es_timeout_seconds,
        )
    services = WorkflowServices(
        source_repo=resolved_source,
        project_subdir=config.project_subdir,
        verify_commands=list(config.verify_commands),
        git_push=config.git_push,
        git_remote=config.git_remote,
        command_timeout_seconds=config.command_timeout_seconds,
        replay_timeout_seconds=config.replay_timeout_seconds,
        workspaces=workspaces,
        delivery=GitBranchDelivery(
            GitDeliveryConfig(
                remote_name=config.git_remote,
                push=config.git_push,
                source_repo=resolved_source,
                git_env=git_auth_env(config.git_username, config.git_password),
                workspace_manager=workspaces,
            )
        ),
        log_source=log_source,
        ignore_errors=list(config.ignore_errors),
        metadata={
            "request_regression": partial(
                run_request_regression,
                python_executable=config.replay_python,
                timeout_seconds=config.command_timeout_seconds,
            ),
            "independent_verify": partial(
                independent_verify,
                timeout_seconds=config.replay_timeout_seconds,
                replay_python=config.replay_python,
            ),
        },
    )
    return services


def _source_sync_node(
    config: RuntimeConfig,
    store: RecordStore,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """同步源仓库作为真正的 LangGraph 节点，使 RetryPolicy 和 checkpoint 能接管失败。"""
    attempts: dict[str, int] = {}

    def node(state: dict[str, Any]) -> dict[str, Any]:
        run_id = str(state.get("run_id") or "")
        if not run_id:
            raise RuntimeError("sync_source_repo 缺少 run_id")
        previous = store.load(run_id, SOURCE_SYNC)
        attempt = (previous.attempt if previous else 0) + 1
        attempts[run_id] = attempts.get(run_id, 0) + 1
        automatic_attempt = attempts[run_id]
        max_attempts = max(0, int(settings.graph_retry_attempts)) + 1
        record = StepRecord(
            name=SOURCE_SYNC,
            title="同步源仓库",
            status="running",
            attempt=attempt,
            started_at=utc_now(),
            input={
                "source_repo_configured": config.source_repo is not None,
                "remote_configured": bool(config.source_url),
            },
        )
        store.save(run_id, record)
        if cancellation_requested(store.root, run_id):
            record.status = "skipped"
            record.result = "SKIPPED"
            record.finished_at = utc_now()
            record.output = {"control_cancelled": True}
            record.summary = "Run 已收到停止请求，未开始同步源仓库"
            store.save(run_id, record)
            return {"last_result": "cancelled", "terminal_status": "CANCELLED"}

        def control_requested() -> bool:
            if cancellation_requested(store.root, run_id):
                return True
            if pause_requested(store.root, run_id):
                raise SourceRepoPaused("Run 已请求暂停，源仓库同步可从检查点重新开始")
            return False

        def report_progress(counts: dict[str, int]) -> None:
            from app.infrastructure.records.lifecycle import LifecycleStore
            downloaded = counts.get("downloaded_bytes", 0) / (1024 * 1024)
            LifecycleStore(store.root).update_run(
                run_id, progress={"step": SOURCE_SYNC,
                                  "message": f"首次克隆 · 第 {automatic_attempt}/{max_attempts} 次尝试 · 已下载 {downloaded:.1f} MiB",
                                  "counts": {**counts, "automatic_attempt": automatic_attempt, "max_attempts": max_attempts}},
                progress_at=utc_now(),
            )

        try:
            control_requested()
            source_repo = sync_source_repo(
                config,
                cancel_requested=control_requested,
                on_progress=report_progress,
            )
        except SourceRepoPaused as error:
            from langgraph.types import interrupt

            record.status = "waiting"
            record.finished_at = utc_now()
            record.summary = public_exception(error, prefix="源仓库同步已暂停")
            record.error = None
            store.save(run_id, record)
            interrupt({"control": "pause", "run_id": run_id, "step": SOURCE_SYNC})
            return node(state)
        except SourceRepoCancelled as error:
            record.status = "skipped"
            record.result = "SKIPPED"
            record.finished_at = utc_now()
            record.duration_ms = 0
            record.output = {"control_cancelled": True}
            record.summary = public_exception(error, prefix="源仓库同步已取消")
            record.error = None
            store.save(run_id, record)
            return {
                "last_result": "cancelled",
                "terminal_status": "CANCELLED",
            }
        except Exception as error:
            record.status = "failed"
            record.finished_at = utc_now()
            record.duration_ms = 0
            record.summary = public_exception(error, prefix="源仓库同步失败")
            record.output = {
                "failure_reason": record.summary,
                "exception_type": type(error).__name__,
                "automatic_attempt": automatic_attempt,
                "max_attempts": max_attempts,
                "retry_count": automatic_attempt - 1,
                "retry_exhausted": automatic_attempt >= max_attempts,
            }
            record.error = StepError(
                code="STEP_EXECUTION_FAILED",
                message=record.summary,
                retryable=True,
            )
            store.save(run_id, record)
            raise
        record.status = "succeeded"
        record.finished_at = utc_now()
        record.output = {"source_repo_path": str(source_repo or "")}
        record.summary = "源仓库同步完成"
        store.save(run_id, record)
        return {"source_repo_path": str(source_repo or ""), "last_result": "passed"}

    node.__name__ = SOURCE_SYNC
    return node


def build_nodes(
    store: RecordStore,
    services: WorkflowServices,
    runtimes: dict[str, Any] | None = None,
    config: RuntimeConfig | None = None,
) -> dict[str, Callable[..., Any]]:
    """按插件契约登记单步节点、编排组合和 agent 节点。"""
    load_all()
    verify_registry()
    overrides = dict(runtimes or {})
    nodes: dict[str, Callable[..., Any]] = {}
    for spec in registry.steps():
        nodes[spec.name] = bind(spec, store, services)
    for agent in registry.agents():
        nodes[agent.name] = build_agent_node(agent, store, overrides.get(agent.kind), services=services)

    # 图上按业务阶段调度；阶段内部仍逐个调用原始插件节点，因此原有步骤记录、
    # artifact、单步调试入口和失败结果语义均保持不变。
    for phase_name, step_names in PHASE_COMPOSITIONS.items():
        nodes[phase_name] = _compose_phase(step_names, nodes, store=store)
    nodes[SOURCE_SYNC] = _source_sync_node(config or load_config(), store)
    return nodes


def _record_is_fresh(record_started_at: Any, continuation_started_at: Any) -> bool:
    """步骤记录是否晚于本次续跑起点；解析失败时退回字符串比较。"""
    if not continuation_started_at:
        return False
    try:
        return datetime.fromisoformat(
            str(record_started_at)
        ) >= datetime.fromisoformat(str(continuation_started_at))
    except ValueError:
        return str(record_started_at) >= str(continuation_started_at)


def _compose_phase(
    step_names: tuple[str, ...],
    nodes: dict[str, Callable[..., Any]],
    store: RecordStore | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """顺序执行一个组合阶段；失败、跳过、人工介入或取消后立即停止后续子步骤。"""
    missing = [name for name in step_names if name not in nodes]
    if missing:
        raise PluginError(f"组合阶段缺少步骤插件: {', '.join(missing)}")

    def node(state: dict[str, Any]) -> dict[str, Any]:
        current = dict(state)
        combined_patch: dict[str, Any] = {}
        continuation = dict(state.get("continuation") or {})
        after = str(continuation.get("after_step") or "")
        selected = (
            step_names[step_names.index(after) + 1 :]
            if after in step_names
            else step_names
        )
        if (
            after in step_names
            and str(state.get("last_result") or "passed") != "passed"
        ):
            return {"continuation": {}}
        for name in selected:
            saved = (
                store.load(str(state["run_id"]), name, str(state.get("task_id") or ""))
                if store and after in step_names
                else None
            )
            if saved and saved.status == "succeeded" and _record_is_fresh(
                saved.started_at, continuation.get("started_at")
            ):
                spec = registry.resolve(name)
                from app.workflow.contracts import Handoff

                formal = Handoff.model_validate(
                    store.read_artifact(
                        str(state["run_id"]),
                        f"workflow/outputs/{name}.json",
                        str(state.get("task_id") or ""),
                    )
                )
                patch = {**spec.patch(formal.outcome.output), "last_result": "passed"}
            else:
                patch = nodes[name](current)
            if not isinstance(patch, dict):
                raise PluginError(f"组合阶段子步骤必须返回 dict: {name}")
            current.update(patch)
            combined_patch.update(patch)
            result = str(patch.get("last_result") or "passed").lower()
            if result != "passed":
                break
            if str(current.get("terminal_status") or "").upper() == "CANCELLED":
                break
        if after in step_names:
            combined_patch["continuation"] = {}
        return combined_patch

    node.__name__ = "_".join(step_names)
    return node


def build_graph(
    store: RecordStore | None = None,
    services: WorkflowServices | None = None,
    runtimes: dict[str, Any] | None = None,
    checkpointer: Any = None,
    config: RuntimeConfig | None = None,
) -> CompiledStateGraph:
    """编译主图。仓库未显式给出时按配置目录新建，不依赖全局单例。

    任务图使用独立检查点，父图只等待当前任务执行至完成或交付审批。
    """
    resolved_config = config or load_config()
    resolved_store = store or RecordStore(resolved_config.runtime_dir)
    if checkpointer is None:
        saver = _checkpoint_saver(resolved_config.runtime_dir)
    else:
        saver = checkpointer
    resolved_services = services or build_services(config=resolved_config)
    nodes = build_nodes(
        resolved_store, resolved_services, runtimes, config=resolved_config
    )
    detached_graph = build_task_graph(
        nodes,
        workspace_manager=getattr(resolved_services, "workspaces", None),
        checkpointer=saver,
        transition_store=resolved_store,
        retry_context_builder=lambda state: capture_retry_context(
            state, resolved_store
        ),
        workspace_root=resolved_config.workspace_root,
    )
    from app.workflow.task_execution import invoke_task, task_config, task_result

    def run_task(state: dict[str, Any]) -> dict[str, Any]:
        state = dict(state)
        task = dict(state.get("task") or {})
        previous_context = task.pop("_retry_context", None)
        state["task"] = task
        if previous_context:
            state["retry_context"] = previous_context
        config = task_config(str(state["run_id"]), str(state["task_id"]))
        cycle = int(state.get("attempt") or 1)
        if cycle > 1:
            config["configurable"]["thread_id"] += f":attempt:{cycle}"
        pointer = (
            resolved_store.task_dir(str(state["run_id"]), str(state["task_id"]))
            / "workflow/checkpoint.json"
        )
        if pointer.is_file():
            saved = resolved_store.read_artifact(
                str(state["run_id"]), "workflow/checkpoint.json", str(state["task_id"])
            )
            if (
                saved.get("metadata", {}).get("continuation")
                and saved.get("metadata", {}).get("task_cycle") == cycle
            ):
                config = saved
        resolved_store.write_artifact(
            str(state["run_id"]),
            "workflow/checkpoint.json",
            config,
            str(state["task_id"]),
        )
        snapshot = detached_graph.get_state(config)
        value = None if snapshot.values else state
        result = invoke_task(detached_graph, value, config)
        return task_result(state, result)

    def collect_task(state: dict[str, Any]) -> dict[str, Any]:
        # 收尾报告可能先于分析分支交付生成；只有子图退出才真正收敛任务生命周期。
        patch = collect(state)
        if state.get("terminal_status") != "WAITING_APPROVAL":
            resolved_store.complete_task(str(state["run_id"]), str(state["task_id"]))
        return patch

    graph = build_state_graph(
        nodes,
        detached_graph,
        checkpointer=saver,
        task_collector=collect_task,
        task_runner=run_task,
    )
    return graph


def resume_task_approval(
    store: RecordStore,
    run_id: str,
    task_id: str,
    payload: dict[str, Any],
    config: RuntimeConfig,
    services: WorkflowServices,
) -> bool:
    """恢复当前版本的独立审批检查点。"""
    from app.workflow.validation import assert_workflow_version

    assert_workflow_version(store.load_run(run_id))
    from langgraph.types import Command

    from app.workflow.task_execution import invoke_task, task_config

    graph = build_task_graph(
        build_nodes(store, services, config=config),
        workspace_manager=getattr(services, "workspaces", None),
        checkpointer=_checkpoint_saver(config.runtime_dir),
        transition_store=store,
        retry_context_builder=lambda state: capture_retry_context(state, store),
        workspace_root=config.workspace_root,
    )
    checkpoint_config = task_config(run_id, task_id)
    pointer = store.task_dir(run_id, task_id) / "workflow" / "checkpoint.json"
    if pointer.is_file():
        checkpoint_config = store.read_artifact(
            run_id, "workflow/checkpoint.json", task_id
        )
    snapshot = graph.get_state(checkpoint_config)
    if not snapshot.values:
        raise StepFailed("缺少当前版本的审批检查点，禁止从旧记录补造执行状态")
    if not any(task.interrupts for task in snapshot.tasks):
        raise StepFailed("该任务检查点没有待恢复的审批")
    if snapshot.values.get("repair_slot_claimed"):
        from app.workflow.steps.claim_repair_slot.node import renew_claim

        renew_claim(store.root, run_id=run_id, task_id=task_id)
    result = invoke_task(graph, Command(resume=payload), checkpoint_config)
    if not result.get("__interrupt__"):
        store.complete_task(run_id, task_id)
    return True


def continue_task_after_step(
    store: RecordStore,
    run_id: str,
    task_id: str,
    step_name: str,
    payload: dict[str, Any],
    config: RuntimeConfig,
    services: WorkflowServices,
) -> dict[str, Any]:
    """Continue the selected task through authoritative routers without rerunning the step."""
    from app.workflow.validation import assert_workflow_version

    assert_workflow_version(store.load_run(run_id))
    import re
    import uuid

    from app.application.step_runner import state_from_records
    from app.workflow.task_execution import invoke_task, task_config

    state = {**state_from_records(store, run_id, task_id), **payload}
    record = store.load(run_id, step_name, task_id)
    state["attempt"] = max(
        int(state.get("attempt") or 1), int(record.attempt if record else 1)
    )
    state["automatic_attempt"] = 1
    state["terminal_status"] = str(payload.get("terminal_status") or "")
    state["task_summary"] = {}
    state["max_attempts"] = int(
        store.load_run(run_id).get("max_attempts") or config.max_attempts
    )
    nodes = build_nodes(store, services, config=config)
    owner = next(
        (name for name, members in PHASE_COMPOSITIONS.items() if step_name in members),
        step_name,
    )
    partial = owner != step_name
    if partial:
        state["continuation"] = {"after_step": step_name, "started_at": utc_now()}
    saver = _checkpoint_saver(config.runtime_dir)
    graph = build_task_graph(
        nodes,
        workspace_manager=getattr(services, "workspaces", None),
        checkpointer=saver,
        entry_node=owner if partial else None,
        transition_store=store,
        retry_context_builder=lambda value: capture_retry_context(value, store),
        workspace_root=config.workspace_root,
    )
    checkpoint = task_config(run_id, task_id)
    cycle = 1
    pointer = store.task_dir(run_id, task_id) / "workflow/checkpoint.json"
    if pointer.is_file():
        old = store.read_artifact(run_id, "workflow/checkpoint.json", task_id)
        match = re.search(
            r":attempt:(\d+)$", str(old.get("configurable", {}).get("thread_id") or "")
        )
        cycle = int(
            old.get("metadata", {}).get("task_cycle")
            or (match.group(1) if match else 1)
        )
    checkpoint["configurable"]["thread_id"] += ":step:" + uuid.uuid4().hex
    checkpoint["metadata"] = {"continuation": True, "task_cycle": cycle}
    store.write_artifact(run_id, "workflow/checkpoint.json", checkpoint, task_id)
    if partial:
        result = invoke_task(graph, state, checkpoint)
    else:
        graph.update_state(checkpoint, state, as_node=owner)
        result = invoke_task(graph, None, checkpoint)
    if not result.get("__interrupt__"):
        store.complete_task(run_id, task_id)
    return result


__all__ = [
    "build_agent_node",
    "build_graph",
    "build_nodes",
    "build_services",
    "close_checkpoints",
    "resume_task_approval",
]
