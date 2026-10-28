"""全图构建、编译与执行编排 (LangGraph Graph & Pipeline Orchestration)。

基于 LangGraph 构建支持断点恢复、独立检查点、确定性门禁与全流程生命周期调度的编排引擎。
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy, interrupt

from config.settings import settings
from core.contracts import (
    AgentSpec,
    ArtifactRef,
    StepError,
    StepRecord,
    dump_model,
    error_code_of,
    map_outcome,
    summary_of,
)
from core.errors import StepFailed
from core.keys import safe_name, utc_now
from infrastructure.es.logs import ESLogAdapter
from infrastructure.git.delivery import GitBranchDelivery, GitDeliveryConfig
from infrastructure.git.source import (
    SourceRepoCancelled,
    SourceRepoError,
    SourceRepoPaused,
    git_auth_env,
    sync_source_repo,
)
from infrastructure.llm.config import RuntimeConfig, load_config, workflow_recursion_limit
from infrastructure.llm.runtime import close_sessions
from infrastructure.storage.policy import debug_artifacts
from infrastructure.storage.redaction import public_exception
from infrastructure.storage.run_control import cancellation_requested, pause_requested
from infrastructure.storage.store import RecordStore
from infrastructure.workspace.safe_cleanup import remove_tree
from infrastructure.workspace.workspace import WorkspaceConfig, WorkspaceManager
from workflow.engine.handoff import (
    RunState,
    WorkflowState,
    apply_step_result,
    capture_retry_context,
    invoke_task,
    outcome_of,
    stage_retry_evidence,
    task_config,
    task_result,
)
from workflow.engine.routes import (
    APPROVE,
    CLAIM,
    FINALIZE,
    IDENTIFY,
    PHASE_COMPOSITIONS,
    PREPARE_CONTEXT,
    REPORT,
    RETRY,
    RETRY_RESET_FAILED,
    REVIEW,
    RUN_PREPARE,
    SOURCE_SYNC,
    SUBMIT,
    TASK_CONDITIONAL_TRANSITIONS,
    TASK_NODE_NAMES,
    TASK_STATIC_TRANSITIONS,
    UNIFIED_FIXER,
    after_identify,
    after_run_prepare,
    after_source_sync,
)
from workflow.engine.runner import (
    PluginError,
    WorkflowServices,
    bind,
    load_all,
    resolve,
    verify_registry,
)

logger = logging.getLogger(__name__)

# 父图内部节点名称
RUN_TASK = "run_task"
TASK_DISPATCH = "task_dispatch"
TASK_COLLECT = "task_collect"

_RUN_NODES = (SOURCE_SYNC, RUN_PREPARE, IDENTIFY)
_MAX_DIFF_SUMMARY_CHARS = 8000

# 线程锁与 SQLite 检查点复用连接池
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
    path = str((runtime_dir / "workflow-checkpoints.sqlite3").resolve())
    with _CHECKPOINT_LOCK:
        connection = _CHECKPOINT_CONNECTIONS.get(path)
        if connection is None:
            runtime_dir.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                path,
                check_same_thread=False,
                timeout=30,
            )
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "PRAGMA busy_timeout=30000"
            )
            _CHECKPOINT_CONNECTIONS[path] = connection
        saver = _CHECKPOINT_SAVERS.get(path)
        if saver is None:
            saver = SqliteSaver(connection)
            saver.setup()
            _CHECKPOINT_SAVERS[path] = saver
        return saver


# ---------------------------------------------------------------------------
# 1. 独立正文重放与验证执行器 (Validation Executor)
# ---------------------------------------------------------------------------









# ---------------------------------------------------------------------------
# 2. Agent 子图节点适配器 (Agent Node Adapters)
# ---------------------------------------------------------------------------

def _diff_summary(state: dict[str, Any]) -> str:
    """直接提供当前源码与只读基线的差异，不依赖历史脚本产物。"""
    from infrastructure.workspace.diff import file_changes

    baseline = str(state.get("baseline_dir") or "")
    if not baseline:
        return "缺少只读基线，无法提供修改前后差异。"
    changes = file_changes(baseline, str(state.get("ai_workspace_path") or ""),
                           list(state.get("changed_files") or []))
    text = json.dumps(changes, ensure_ascii=False)
    return text if len(text) <= _MAX_DIFF_SUMMARY_CHARS else text[:_MAX_DIFF_SUMMARY_CHARS] + "…(已截断，请读取基线与当前源码)"


def build_agent_node(
    spec: AgentSpec,
    store: RecordStore,
    runtime: Any = None,
    services: Any = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """构建单一 Agent 闭环节点。"""
    kind = spec.kind
    if kind == "unified_fixer":
        from agents.unified_fixer.graph import build_graph as build
    elif kind == "review":
        from agents.review.graph import build
    else:
        raise PluginError(f"未注册的 Agent 子图: {kind}")

    model_node = _agent_node(spec, build(runtime), store)
    return model_node


def _agent_node(
    spec: AgentSpec, graph: CompiledStateGraph, store: RecordStore
) -> Callable[..., Any]:
    kind = spec.kind
    view_model = spec.output_model
    input_model = spec.input_model

    def node(state: dict[str, Any]) -> dict[str, Any]:
        run_id = str(state.get("run_id") or "")
        task_id = str(state.get("task_id") or "")
        if pause_requested(store.root, run_id) and not state.get("_single_step"):
            waiting = StepRecord(
                name=spec.name,
                title=spec.title,
                status="waiting",
                started_at=utc_now(),
                attempt=int(state.get("attempt") or 1),
                input={"control": "pause_requested"},
            )
            store.save(run_id, waiting, task_id)
            interrupt({"control": "pause", "run_id": run_id, "step": spec.name})
        if cancellation_requested(store.root, run_id):
            return {
                "last_result": "cancelled",
                "terminal_status": "CANCELLED",
                "next_step": FINALIZE,
            }
        task = dict(state.get("task") or {})
        started = time.perf_counter()
        try:
            if state.get("retry_context"):
                state = {**state, "retry_context": stage_retry_evidence(store, state)}
            if kind == "unified_fixer":
                incoming = input_model(
                    retry_context=dict(state.get("retry_context") or {}),
                    run_id=str(state.get("run_id") or ""),
                    task_id=str(state.get("task_id") or ""),
                    attempt=int(state.get("attempt") or 1),
                    carrier=str(task.get("carrier") or ""),
                    biztype=str(task.get("biztype") or ""),
                    service_name=str(task.get("service_name") or ""),
                    problem=dict(state.get("problem") or task.get("problem") or {}),
                    evidence=dict(state.get("evidence") or {}),
                    workspace_path=str(state.get("ai_workspace_path") or ""),
                    source_project_path=str(state.get("source_project_path") or ""),
                    source_snapshot={
                        "baseline_dir": str(state.get("baseline_dir") or ""),
                        "baseline_manifest": dict(state.get("baseline_manifest") or {}),
                    },
                    retry_reason=str(state.get("retry_reason") or ""),
                )
                graph_input = {
                    **task,
                    **dump_model(incoming),
                    "ai_workspace_path": str(state.get("ai_workspace_path") or ""),
                    "run_id": str(state["run_id"]),
                    "baseline_manifest": dict(state.get("baseline_manifest") or {}),
                    "baseline_dir": str(state.get("baseline_dir") or ""),
                    "source_project_path": str(state.get("source_project_path") or ""),
                    "agent_policy": dict(state.get("agent_policy") or {}),
                }
            else:
                from agents.unified_fixer.models import RepairView
                repair = state.get("repair")
                repair_payload = repair if isinstance(repair, dict) else {}
                incoming = input_model(
                    retry_context=dict(state.get("retry_context") or {}),
                    task_id=str(state.get("task_id") or ""),
                    attempt=int(state.get("attempt") or 1),
                    ai_workspace_path=str(state.get("ai_workspace_path") or ""),
                    repair=RepairView.model_validate(repair_payload) if repair_payload else RepairView(),
                    changed_files=[str(item) for item in (state.get("changed_files") or [])],
                    diff_summary=_diff_summary(state),
                    baseline_dir=str(state.get("baseline_dir") or ""),
                )
                graph_input = {
                    **task,
                    **dump_model(incoming),
                    "repair": repair_payload,
                    "problem": dict(state.get("problem") or task.get("problem") or {}),
                    "run_id": str(state["run_id"]),
                    "baseline_dir": str(state.get("baseline_dir") or ""),
                    "baseline_manifest": dict(state.get("baseline_manifest") or {}),
                    "framework": str(state.get("framework") or (state.get("evidence") or {}).get("framework") or ""),
                    "source_project_path": str(state.get("source_project_path") or ""),
                }
        except Exception as error:
            record = StepRecord(
                name=spec.name,
                title=spec.title,
                status="failed",
                started_at=utc_now(),
                attempt=int(state.get("attempt") or 1),
                input={"failure_stage": "input_validation"},
            )
            record.error = StepError(
                code=error_code_of("system_crash"),
                message=public_exception(error, prefix="Agent 输入构造失败"),
            )
            record.output = {"failure_reason": public_exception(error, prefix="Agent 输入构造失败")}
            record.finished_at = utc_now()
            record.duration_ms = round((time.perf_counter() - started) * 1000, 2)
            store.save(run_id, record, task_id)
            patch = {
                "last_result": "failed",
                "retry_reason": public_exception(error, prefix=f"{spec.name} 输入构造失败"),
                "retry_target": spec.name,
                "terminal_status": "failed",
                "attempt": int(state.get("attempt") or 1),
            }
            return apply_step_result(
                store,
                state,
                spec.name,
                patch,
                outcome_of(
                    record.output,
                    execution_status="failed",
                    summary=record.error.message,
                    error={
                        "code": "AGENT_INPUT_INVALID",
                        "message": record.error.message,
                        "retryable": False,
                    },
                ),
            )

        record = StepRecord(
            name=spec.name,
            title=spec.title,
            status="running",
            started_at=utc_now(),
            attempt=int(state.get("attempt") or 1),
            input=dump_model(incoming),
        )
        refs: list[ArtifactRef] = []
        try:
            store.save(
                run_id,
                record,
                task_id,
                input_view=spec.input_record,
                output_view=spec.output_record,
            )
            final = graph.invoke(graph_input)
            record.duration_ms = round((time.perf_counter() - started) * 1000, 2)

            if debug_artifacts(store.run_dir(run_id)):
                refs.append(
                    ArtifactRef(
                        name=f"{spec.name}.json",
                        path=store.write_artifact(
                            run_id,
                            f"agents/{spec.name}.json",
                            {
                                "agent": spec.name,
                                "attempt": int(state.get("attempt") or 1),
                                "result": str(final.get("raw_result") or ""),
                            },
                            task_id,
                        ),
                        task_id=task_id,
                    )
                )

            view = view_model.model_validate(
                {key: final[key] for key in view_model.model_fields if key in final}
            )

            if kind == "unified_fixer":
                is_passed = (
                    bool(final.get("passed"))
                    or str(final.get("result") or "").lower() == "passed"
                )
                # Agent 自身的状态与步骤业务结果使用不同契约。
                # 仅分析虽然 result=passed，也仍然需要人工处理。
                conclusion = {
                    "ANALYSIS_ONLY": "MANUAL",
                    "MANUAL": "MANUAL",
                    "INSUFFICIENT_EVIDENCE": "INSUFFICIENT_EVIDENCE",
                    "FIXED": "PASS" if is_passed else "FAIL",
                }[view.status]
            else:
                raw_verdict = str(final.get("verdict") or "").upper()
                is_passed = bool(final.get("passed")) or raw_verdict == "PASS"
                verdict = "MANUAL" if raw_verdict == "MANUAL" else ("PASS" if is_passed else "FAIL")
                view = view.model_copy(update={"passed": is_passed, "verdict": verdict})
                conclusion = str(view.verdict)

            if kind == "review" and state.get("baseline_dir"):
                diff_files = []
                for name in state.get("changed_files") or []:
                    entry = {"path": name, "available": True}
                    for key, root in (("before", state["baseline_dir"]), ("after", state["ai_workspace_path"])):
                        base = Path(root).resolve()
                        target = (base / name).resolve()
                        if not target.is_relative_to(base):
                            raise ValueError("差异文件越出源码范围")
                        entry[key] = target.read_text(encoding="utf-8") if target.is_file() else ""
                    diff_files.append(entry)
                refs.append(ArtifactRef(name="diff", path=store.write_artifact(
                    run_id, "artifacts/diff.json", {"files": diff_files}, task_id), task_id=task_id))

            refs.append(
                ArtifactRef(
                    name="validated_output",
                    path=store.write_artifact(
                        run_id,
                        f"agents/{spec.name}.validated.json",
                        dump_model(view),
                        task_id,
                    ),
                    task_id=task_id,
                )
            )

            status, business = map_outcome(kind="ok", business=conclusion)
            record.status = status
            record.result = business
            record.output = dump_model(view)
            record.summary = summary_of(record.output) or str(view.analysis or view.summary)
            audit = final.get("_audit") if isinstance(final, dict) else None
            if isinstance(audit, dict) and audit:
                refs.append(
                    ArtifactRef(
                        name="agent.jsonl",
                        path=store.append_artifact(
                            run_id,
                            "metrics/agent.jsonl",
                            {
                                **audit,
                                "tool_call_count": int(audit.get("tool_call_count") or 0),
                                "duration_ms": record.duration_ms,
                                "status": record.status,
                                "result": record.result,
                            },
                            task_id,
                        ),
                        task_id=task_id,
                    )
                )
            record.artifacts = list(refs)
        except Exception as error:
            record.status = "failed"
            record.result = None
            record.output = {"failure_reason": public_exception(error, prefix="Agent 执行异常")}
            record.artifacts = list(refs)
            record.error = StepError(
                code=error_code_of("system_crash"),
                message=public_exception(error, prefix="Agent 内部运行系统级崩溃"),
            )
            record.finished_at = utc_now()
            record.duration_ms = round((time.perf_counter() - started) * 1000, 2)
            store.save(run_id, record, task_id)
            patch = {
                "last_result": "failed",
                "retry_reason": public_exception(error, prefix=f"{spec.name} 执行异常"),
                "retry_target": spec.name,
                "terminal_status": "failed",
                "attempt": int(state.get("attempt") or 1),
            }
            return apply_step_result(
                store,
                state,
                spec.name,
                patch,
                outcome_of(
                    record.output,
                    execution_status="failed",
                    summary=record.summary,
                    error={
                        "code": "AGENT_EXECUTION_FAILED",
                        "message": record.error.message,
                        "retryable": True,
                    },
                ),
            )

        record.finished_at = utc_now()
        formal = outcome_of(record.output, summary=record.summary, business=record.result)
        record.execution, record.assessment = formal.execution, formal.assessment
        store.save(
            run_id,
            record,
            task_id,
            input_view=spec.input_record,
            output_view=spec.output_record,
        )

        if kind == "unified_fixer":
            failed = view.result == "failed" and view.status not in {"ANALYSIS_ONLY", "MANUAL"}
            reason = view.summary or view.analysis
        else:
            failed = view.verdict == "FAIL"
            reason = "\n".join(
                filter(
                    None,
                    [
                        view.summary,
                        f"发现项: {', '.join(view.findings)}" if view.findings else "",
                        f"要求: {', '.join(view.required_changes)}" if view.required_changes else "",
                    ],
                )
            )

        patch: dict[str, Any] = {
            kind: view.model_dump(),
            "last_result": "failed" if failed else "passed",
            "next_step": None,
            "retry_reason": reason if failed else "",
            "retry_target": "",
            "attempt": int(state.get("attempt") or 1),
            "terminal_status": (
                "manual"
                if (kind == "unified_fixer" and view.status in {"MANUAL", "ANALYSIS_ONLY"})
                or (kind == "review" and str(view.verdict or "").upper() == "MANUAL")
                else "failed"
                if failed
                else "passed"
            ),
        }

        if kind == "unified_fixer":
            changed_files = [str(item) for item in (final.get("changed_files") or [])]
            change_plan = (
                view.change_plan.model_dump(mode="json") if view.change_plan is not None else {}
            )
            patch["changed_files"] = changed_files
            patch["repair_changed_files"] = changed_files
            patch["change_plan"] = change_plan
            patch["blocked_reason"] = str(final.get("blocked_reason") or "")
            patch["analysis_only"] = view.status in {"ANALYSIS_ONLY", "MANUAL"}
            patch["locate"] = dict(final.get("locate") or {})
            patch["repair"] = dict(final.get("repair") or {})
            if failed:
                patch["retry_target"] = "unified_fixer_agent"
        elif kind == "review":
            patch["review_contract_error"] = view.contract_error
            patch["terminal_status"] = "passed"
            if failed:
                patch["retry_target"] = "unified_fixer_agent"

        if cancellation_requested(store.root, run_id):
            patch.update(
                {
                    "last_result": "cancelled",
                    "terminal_status": "CANCELLED",
                    "next_step": FINALIZE,
                }
            )

        return apply_step_result(
            store,
            state,
            spec.name,
            patch,
            outcome_of(
                record.output,
                summary=record.summary,
                business=record.result,
                artifacts=[ref.path for ref in record.artifacts],
            ),
        )

    node.__name__ = spec.name
    return node


# ---------------------------------------------------------------------------
# 3. 依赖服务装配 (Workflow Services Assembly)
# ---------------------------------------------------------------------------

def build_services(
    config: RuntimeConfig | None = None,
    source_repo: Path | None = None,
) -> WorkflowServices:
    """按当前配置装配外部依赖。缺配置的字段保持空，对应步骤运行时失败。"""
    from infrastructure.git.source import git_auth_env
    from infrastructure.llm.config import create_es_client, index_days

    config = config or load_config()
    resolved_source = source_repo if source_repo is not None else config.source_repo
    workspaces = WorkspaceManager(
        WorkspaceConfig(root_dir=config.workspace_root, project_subdir=config.project_subdir)
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
        git_push=config.git_push,
        git_remote=config.git_remote,
        command_timeout_seconds=config.command_timeout_seconds,
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

    )
    return services


# ---------------------------------------------------------------------------
# 4. 节点构建与组合阶段编排 (Nodes Building)
# ---------------------------------------------------------------------------

def _source_sync_node(
    config: RuntimeConfig,
    store: RecordStore,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    attempts: dict[str, int] = {}

    def node(state: dict[str, Any]) -> dict[str, Any]:
        run_id = str(state.get("run_id") or "")
        if not run_id:
            raise RuntimeError("sync_source_repo 缺少 run_id")
        previous = store.load(run_id, SOURCE_SYNC)
        attempt = (previous.attempt if previous else 0) + 1
        attempts[run_id] = attempts.get(run_id, 0) + 1
        automatic_attempt = attempts[run_id]
        max_attempts = 2 + 1
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
            downloaded = counts.get("downloaded_bytes", 0) / (1024 * 1024)
            phase = "拉取源码更新" if counts.get("fetching") else "下载源码"
            message = f"{phase} · 第 {automatic_attempt}/{max_attempts} 次尝试 · 已下载 {downloaded:.1f} MiB"
            if "downloaded_bytes" not in counts:
                message = "正在准备源码同步，等待 Git 下载进度"
            store.report_progress(
                run_id, SOURCE_SYNC,
                message,
                {**counts, "automatic_attempt": automatic_attempt, "max_attempts": max_attempts},
            )

        try:
            control_requested()
            report_progress({})
            source_repo = sync_source_repo(
                config,
                cancel_requested=control_requested,
                on_progress=report_progress,
            )
        except SourceRepoPaused as error:
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
            return {"last_result": "cancelled", "terminal_status": "CANCELLED"}
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


def _record_is_fresh(record_started_at: Any, continuation_started_at: Any) -> bool:
    if not continuation_started_at:
        return False
    try:
        return datetime.fromisoformat(str(record_started_at)) >= datetime.fromisoformat(str(continuation_started_at))
    except ValueError:
        return str(record_started_at) >= str(continuation_started_at)


def _compose_phase(
    step_names: tuple[str, ...],
    nodes: dict[str, Callable[..., Any]],
    store: RecordStore | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
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
        if after in step_names and str(state.get("last_result") or "") != "passed":
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
                spec = resolve(name)
                from core.contracts import Handoff
                try:
                    formal = Handoff.model_validate(
                        store.read_artifact(
                            str(state["run_id"]),
                            f"workflow/outputs/{name}.json",
                            str(state.get("task_id") or ""),
                        )
                    )
                except StepFailed:
                    patch = nodes[name](current)
                else:
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


def build_nodes(
    store: RecordStore,
    services: WorkflowServices,
    runtimes: dict[str, Any] | None = None,
    config: RuntimeConfig | None = None,
) -> dict[str, Callable[..., Any]]:
    from workflow.engine.runner import agents as registered_agents, steps as registered_steps

    load_all()
    verify_registry()
    overrides = dict(runtimes or {})
    nodes: dict[str, Callable[..., Any]] = {}
    for spec in registered_steps():
        nodes[spec.name] = bind(spec, store, services)
    for agent in registered_agents():
        nodes[agent.name] = build_agent_node(agent, store, overrides.get(agent.kind), services=services)

    for phase_name, step_names in PHASE_COMPOSITIONS.items():
        nodes[phase_name] = _compose_phase(step_names, nodes, store=store)
    nodes[SOURCE_SYNC] = _source_sync_node(config or load_config(), store)
    return nodes


# ---------------------------------------------------------------------------
# 5. LangGraph 主图与任务子图构建 (Graph Assembly)
# ---------------------------------------------------------------------------

class _RetryableNodeFailure(RuntimeError):
    pass


def _retry_failed_result(name: str, node: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(state: dict[str, Any]) -> dict[str, Any]:
        result = node(state)
        if isinstance(result, dict) and str(result.get("last_result") or "").lower() == "failed":
            reason = str(result.get("retry_reason") or result.get("terminal_reason") or "节点返回失败结论")
            raise _RetryableNodeFailure(f"{name}: {reason[:1200]}")
        return result

    wrapped.__name__ = name
    return wrapped


def _transient_retry() -> RetryPolicy:
    retry_count = 2
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


def kept_repair_slot(state: dict[str, Any]) -> bool:
    return bool(state.get("repair_slot_claimed"))


def dispatch(state: RunState) -> dict[str, Any]:
    tasks = list(state.get("tasks") or [])
    cursor = int(state.get("task_cursor") or 0)
    if cursor >= len(tasks):
        return {"last_result": "passed"}
    task = dict(tasks[cursor])
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
    tasks = list(state.get("tasks") or [])
    return RUN_TASK if int(state.get("task_cursor") or 0) < len(tasks) else END


def collect(state: RunState) -> dict[str, Any]:
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
    """编译任务子图。"""
    graph: StateGraph = StateGraph(WorkflowState)
    for name in TASK_NODE_NAMES:
        if name == RETRY:
            def retry_node(state: dict[str, Any]) -> dict[str, Any]:
                target = str(state.get("retry_target") or "")
                try:
                    retry_context = (retry_context_builder or capture_retry_context)(state)
                    if retry_context.get("capture_errors"):
                        raise StepFailed("重试证据采集不完整：" + "; ".join(retry_context["capture_errors"]))
                except Exception as error:
                    reason = public_exception(error, prefix="保存上一轮重试证据失败，保留当前工作区")
                    return {
                        "last_result": "failed",
                        "terminal_status": "FAILED",
                        "retry_target": RETRY_RESET_FAILED,
                        "retry_reason": reason,
                        "blocked_reason": reason,
                    }
                manager = workspace_manager
                preserve_workspace_targets = {CLAIM, REVIEW, REPORT, SUBMIT}
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
                            raise RuntimeError("缺少 run_id/task_id，无法定位旧的任务基线")
                        root = Path(workspace_root).resolve() if workspace_root is not None else None
                        if root is None:
                            raise RuntimeError("工作区根目录未配置")
                        if transition_store is not None and hasattr(transition_store, "task_dir"):
                            task_root = Path(transition_store.task_dir(run_id, task_id)).resolve()
                        else:
                            task_root = (root.parent / "runs" / safe_name(run_id) / "tasks" / safe_name(task_id)).resolve()
                        baseline_value = str(state.get("baseline_dir") or task_root / "source_snapshot")
                        baseline = Path(baseline_value).resolve()
                        if not baseline.is_relative_to(task_root):
                            raise RuntimeError("源码基线超出当前 Task 目录")
                        workspace_value = str(state.get("ai_workspace_path") or "")
                        workspace = (
                            Path(workspace_value).resolve()
                            if workspace_value
                            else (root / "ai" / manager.safe_name(f"{run_id}-{task_id}")).resolve()
                        )
                        if not workspace.is_relative_to(workspace_root):
                            raise RuntimeError("隔离工作区超出配置的 workspace_root")
                        suffix = f".previous-attempt-{int(state.get('attempt') or 1) + 1}-{uuid.uuid4().hex[:8]}"
                        for original in (baseline, workspace):
                            if original.exists():
                                archive = original.with_name(original.name + suffix)
                                original.replace(archive)
                                archived.append((original, archive))
                        preserve_workspace = True
                    except Exception as error:
                        for original, archive in reversed(archived):
                            try:
                                if archive.exists() and not original.exists():
                                    archive.replace(original)
                            except OSError:
                                pass
                        return {
                            "attempt": int(state.get("attempt") or 1),
                            "last_result": "failed",
                            "terminal_status": "failed",
                            "retry_target": RETRY_RESET_FAILED,
                            "retry_reason": public_exception(error, prefix="重试准备阶段前归档旧基线/工作区失败"),
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
                        manifest = {str(k): str(v) for k, v in (state.get("baseline_manifest") or {}).items()}
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
                            "retry_reason": public_exception(error, prefix="重试前恢复隔离工作区失败"),
                        }
                patch: dict[str, Any] = {
                    "retry_context": retry_context,
                    "attempt": int(state.get("attempt") or 1) + 1,
                    "automatic_attempt": int(state.get("automatic_attempt") or 1) + (1 if target == UNIFIED_FIXER else 0),
                    "validation_recovery_counts": {
                        **dict(state.get("validation_recovery_counts") or {}),
                        target: int((state.get("validation_recovery_counts") or {}).get(target, 0)) + 1,
                    },
                    "terminal_status": "passed",
                    "decision": "PENDING",
                    "review": dict(state.get("review") or {}) if target in {REPORT, SUBMIT} else {},
                    "delivery": {} if target != SUBMIT else state.get("delivery", {}),
                    "approval_result": state.get("approval_result", {}) if target == SUBMIT else {},
                    "approval_granted": bool(state.get("approval_granted")) if target == SUBMIT else False,
                    "delivery_candidate": state.get("delivery_candidate", {}) if target in {SUBMIT, REPORT} else {},
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
                if target not in {REVIEW, REPORT, SUBMIT}:
                    patch.update({
                        "repair": {},
                        "changed_files": [],
                        "repair_changed_files": [],
                        "change_plan": {},
                        "scope_escalation_required": False,
                    })
                if target == PREPARE_CONTEXT:
                    patch.update({
                        "baseline_manifest": {},
                        "baseline_dir": "",
                        "source_project_path": "",
                        "ai_workspace_path": "",
                        "evidence": {},
                        "framework": "",
                        "locate": {},
                    })
                if target == UNIFIED_FIXER:
                    patch["unified_fixer"] = {}
                    patch["locate"] = {}
                    patch["repair"] = {}
                if transition_store is not None:
                    return apply_step_result(
                        transition_store,
                        state,
                        RETRY,
                        patch,
                        outcome_of({"target": target, "reason": state.get("retry_reason", "")}),
                    )
                return patch

            graph.add_node(name, retry_node)
        else:
            graph.add_node(name, nodes[name])

    for source, target in TASK_STATIC_TRANSITIONS:
        graph.add_edge(source, entry_node if source == START and entry_node else target)
    for source, router, targets in TASK_CONDITIONAL_TRANSITIONS:
        graph.add_conditional_edges(source, router, list(targets))

    return graph.compile(checkpointer=checkpointer)


def build_graph(
    store: RecordStore | None = None,
    services: WorkflowServices | None = None,
    runtimes: dict[str, Any] | None = None,
    checkpointer: Any = None,
    config: RuntimeConfig | None = None,
) -> CompiledStateGraph:
    """编译 run 级主图与嵌套任务执行架构。"""
    resolved_config = config or load_config()
    resolved_store = store or RecordStore(resolved_config.runtime_dir)
    saver = _checkpoint_saver(resolved_config.runtime_dir) if checkpointer is None else checkpointer
    resolved_services = services or build_services(config=resolved_config)
    nodes = build_nodes(resolved_store, resolved_services, runtimes, config=resolved_config)
    detached_graph = build_task_graph(
        nodes,
        workspace_manager=getattr(resolved_services, "workspaces", None),
        checkpointer=saver,
        transition_store=resolved_store,
        retry_context_builder=lambda state: capture_retry_context(state, resolved_store),
        workspace_root=resolved_config.workspace_root,
    )

    def run_task(state: dict[str, Any]) -> dict[str, Any]:
        state = dict(state)
        task = dict(state.get("task") or {})
        previous_context = task.pop("_retry_context", None)
        state["task"] = task
        if previous_context:
            state["retry_context"] = previous_context
        config_dict = task_config(str(state["run_id"]), str(state["task_id"]))
        cycle = int(state.get("attempt") or 1)
        if cycle > 1:
            config_dict["configurable"]["thread_id"] += f":attempt:{cycle}"
        pointer = resolved_store.task_dir(str(state["run_id"]), str(state["task_id"])) / "workflow/checkpoint.json"
        if pointer.is_file():
            saved = resolved_store.read_artifact(str(state["run_id"]), "workflow/checkpoint.json", str(state["task_id"]))
            if saved.get("metadata", {}).get("continuation") and saved.get("metadata", {}).get("task_cycle") == cycle:
                config_dict = saved
        resolved_store.write_artifact(str(state["run_id"]), "workflow/checkpoint.json", config_dict, str(state["task_id"]))
        snapshot = detached_graph.get_state(config_dict)
        value = None if snapshot.values else state
        result = invoke_task(detached_graph, value, config_dict)
        return task_result(state, result)

    def collect_task(state: dict[str, Any]) -> dict[str, Any]:
        patch = collect(state)
        if state.get("terminal_status") != "WAITING_APPROVAL":
            resolved_store.complete_task(str(state["run_id"]), str(state["task_id"]))
        return patch

    graph: StateGraph = StateGraph(RunState)
    retry = _transient_retry()
    for name in _RUN_NODES:
        handler = _retry_failed_result(name, nodes[name]) if name in {RUN_PREPARE, IDENTIFY} else nodes[name]
        graph.add_node(name, handler, retry_policy=retry if name in {SOURCE_SYNC, RUN_PREPARE, IDENTIFY} else None)
    graph.add_node(TASK_DISPATCH, dispatch)
    graph.add_node(RUN_TASK, run_task)
    graph.add_node(TASK_COLLECT, collect_task)

    graph.add_edge(START, SOURCE_SYNC)
    graph.add_conditional_edges(SOURCE_SYNC, after_source_sync, [RUN_PREPARE, END])
    graph.add_conditional_edges(RUN_PREPARE, after_run_prepare, [IDENTIFY, END])
    graph.add_conditional_edges(IDENTIFY, after_identify, [TASK_DISPATCH, END])
    graph.add_conditional_edges(TASK_DISPATCH, after_dispatch, [RUN_TASK, END])
    graph.add_edge(RUN_TASK, TASK_COLLECT)
    graph.add_edge(TASK_COLLECT, TASK_DISPATCH)

    return graph.compile(checkpointer=saver)


def resume_task_approval(
    store: RecordStore,
    run_id: str,
    task_id: str,
    payload: dict[str, Any],
    config: RuntimeConfig,
    services: WorkflowServices,
) -> bool:
    """恢复当前版本的独立审批检查点。"""
    from langgraph.types import Command
    from workflow.engine.routes import assert_workflow_version

    assert_workflow_version(store.load_run(run_id))
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
        checkpoint_config = store.read_artifact(run_id, "workflow/checkpoint.json", task_id)
    snapshot = graph.get_state(checkpoint_config)
    if not snapshot.values:
        raise StepFailed("缺少当前版本的审批检查点，禁止从旧记录补造执行状态")
    if not any(task.interrupts for task in snapshot.tasks):
        raise StepFailed("该任务检查点没有待恢复的审批")
    if snapshot.values.get("repair_slot_claimed"):
        from workflow.stages.claim_slot.node import renew_claim
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
    """单步执行后推进后续流程。"""
    import re
    from workflow.engine.routes import assert_workflow_version
    from workflow.engine.runner import state_from_records

    assert_workflow_version(store.load_run(run_id))
    state = {**state_from_records(store, run_id, task_id), **payload}
    record = store.load(run_id, step_name, task_id)
    state["attempt"] = max(int(state.get("attempt") or 1), int(record.attempt if record else 1))
    state.setdefault("automatic_attempt", 1)
    state["terminal_status"] = str(payload.get("terminal_status") or "")
    state["task_summary"] = {}
    state["max_attempts"] = int(store.load_run(run_id).get("max_attempts") or config.max_attempts)
    nodes = build_nodes(store, services, config=config)
    owner = next((name for name, members in PHASE_COMPOSITIONS.items() if step_name in members), step_name)
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
        match = re.search(r":attempt:(\d+)$", str(old.get("configurable", {}).get("thread_id") or ""))
        cycle = int(old.get("metadata", {}).get("task_cycle") or (match.group(1) if match else 1))
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


# ---------------------------------------------------------------------------
# 6. 工作流完整运行入口 (Entrypoint Execution)
# ---------------------------------------------------------------------------

def prepare_run(
    run_id: str | None = None,
    *,
    overrides: dict[str, Any] | None = None,
    queued: bool = False,
    allow_prepared: bool = False,
) -> tuple[str, Any, RecordStore]:
    """同步创建可查询的 Run 元数据。"""
    config = load_config()
    options = dict(overrides or {})
    parameters = config.run_parameters(options)
    config = replace(
        config,
        log_hours=parameters["log_hours"],
        limit=parameters["log_limit"],
        review_mode=parameters["review_mode"],
        max_attempts=parameters["max_attempts"],
    )
    if (
        not 1 <= config.log_hours <= 720
        or not 1 <= config.max_attempts <= 5
        or config.limit < 0
    ):
        raise ValueError("执行参数超出允许范围")
    if config.review_mode not in ("agent", "human"):
        raise ValueError("review_mode 只能是 agent 或 human")
    if config.source_repo is None:
        raise RuntimeError("AI_REPAIR_SOURCE_URL 或 AI_REPAIR_SOURCE_REPO 未配置，禁止启动完整修复工作流")

    resolved = run_id or uuid.uuid4().hex
    store = RecordStore(config.runtime_dir)
    metadata = {
        "workflow_version": 7,
        "source_repo_path": str(config.source_repo),
        "project_subdir": str(config.project_subdir or ""),
        "log_service_name": config.log_service_name,
        "log_service_prefix": config.log_service_prefix,
        "log_excluded_messages": list(config.excluded_messages),
        "log_excluded_biztypes": list(config.excluded_biztypes),
        "log_hours": config.log_hours,
        "log_limit": config.limit,
        "review_mode": config.review_mode,
        "max_attempts": config.max_attempts,
        "allow_push": config.git_push,
        "require_push": False,
        "startup_state": "queued" if queued else "started",
    }
    if "agent_policy" in options and isinstance(options["agent_policy"], dict):
        metadata["agent_policy"] = options["agent_policy"]
    run_json = store.run_dir(resolved) / "run.json"
    if run_json.is_file():
        if not allow_prepared:
            raise RuntimeError(f"执行 {resolved} 已存在，禁止覆盖或重复启动")
        existing = store.load_run(resolved)
        if existing.get("startup_state") != "queued":
            raise RuntimeError(f"执行 {resolved} 不是待启动状态，拒绝重复启动")
        mismatches = [
            key for key, value in metadata.items()
            if key != "startup_state" and existing.get(key) != value
        ]
        if mismatches:
            raise RuntimeError(f"执行 {resolved} 已存在，但配置不匹配：{', '.join(mismatches)}")
        existing["startup_state"] = "started"
        temporary = run_json.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(run_json)
    else:
        if allow_prepared:
            raise RuntimeError(f"执行 {resolved} 缺少 API 预创建的运行记录，拒绝后台启动")
        store.create_run(resolved, metadata)
    return resolved, config, store


def run_workflow(
    run_id: str | None = None,
    *,
    overrides: dict[str, Any] | None = None,
    prepared: bool = False,
) -> dict[str, Any]:
    """执行一次完整工作流。"""
    resolved, config, store = prepare_run(
        run_id, overrides=overrides, allow_prepared=prepared
    )
    try:
        source_repo = config.source_repo
        graph = build_graph(
            store=store,
            services=build_services(config=config, source_repo=source_repo),
            config=config,
        )
        final_state = graph.invoke(
            {
                "schema_version": 2,
                "run_id": resolved,
                "source_repo_path": str(source_repo or ""),
                "log_service_name": config.log_service_name,
                "log_service_prefix": config.log_service_prefix,
                "log_excluded_messages": list(config.excluded_messages),
                "log_excluded_biztypes": list(config.excluded_biztypes),
                "log_hours": config.log_hours,
                "log_limit": config.limit,
                "workflow_version": 7,
                "attempt": 1,
                "max_attempts": config.max_attempts,
                "review_mode": config.review_mode,
                "allow_push": config.git_push,
                "require_push": False,
            },
            config={
                "configurable": {"thread_id": resolved},
                "recursion_limit": workflow_recursion_limit(
                    max_attempts=config.max_attempts,
                    task_count_hint=10,
                ),
            },
        )
        _write_run_report_if_terminal(store, resolved, graph, final_state=final_state)
        return final_state
    except Exception as exc:
        _write_run_report(store, resolved, error=exc)
        raise
    finally:
        close_sessions(resolved, None)


def _uncaught_reason(error: BaseException) -> str:
    """未进入步骤记录的异常只留类型，不把命令原文或密钥写进报告。"""
    from infrastructure.git.source import SourceRepoError

    if isinstance(error, SourceRepoError):
        return public_exception(error, prefix="源仓库同步失败")
    name = type(error).__name__ or "Exception"
    return f"工作流未捕获异常（错误类型：{name}）。完整堆栈只在服务端日志。"


def _ensure_task_final_report(
    store: Any,
    run_id: str,
    final_state: dict[str, Any],
    task_id: str,
    existing: dict[str, Any],
    *,
    finished_at: str,
    error: BaseException | None = None,
) -> dict[str, Any]:
    all_records = store.list_steps(run_id, task_id)
    finalizer = next((record for record in reversed(all_records) if record.name == "finalize_execution"), None)
    final_output = finalizer.output if finalizer and isinstance(finalizer.output, dict) else {}

    result = str(final_output.get("delivery_status") or existing.get("result") or "").upper()
    terminal_status = str(result or existing.get("terminal_status")).upper()

    if not result:
        is_cancelled = False
        latest = all_records[-1] if all_records else None
        if latest is not None:
            if latest.status == "skipped" and latest.output.get("control_cancelled"):
                is_cancelled = True
            elif latest.status == "waiting":
                is_cancelled = False
            elif str(latest.result or "").upper() in {"FAIL", "FAILED", "REJECTED"} or latest.status == "failed":
                result = "FAILED"
            else:
                result = "SUCCESS"
        if is_cancelled:
            result = "CANCELLED"
            terminal_status = "CANCELLED"
        elif not result:
            result = "FAILED"

    task = dict(final_state.get("task") or {})
    carrier = str(existing.get("carrier") or task.get("carrier") or "")
    biztype = str(existing.get("biztype") or task.get("biztype") or "")
    locate = dict(final_state.get("locate") or {})
    repair = dict(final_state.get("repair") or {})
    terminal_step, inferred_reason, inferred_action = "", "", ""
    if all_records:
        from workflow.stages.finalize.node import _terminal_details
        terminal_step, inferred_reason, inferred_action = _terminal_details(all_records, "", result)
    else:
        inferred_reason = "任务已结束，但系统未能从持久化步骤记录还原具体结束原因。"
        inferred_action = "查看最后一个步骤的输入、输出和错误，补齐处置记录后再重试或转人工。"

    terminal_reason = str(final_output.get("terminal_reason") or inferred_reason or existing.get("terminal_reason") or "").strip()
    next_action = str(final_output.get("next_action") or inferred_action or existing.get("next_action") or "").strip()
    if error is not None and result == "FAILED" and not terminal_reason:
        terminal_reason = _uncaught_reason(error)
        next_action = "检查失败步骤的错误类型和任务产物；修复后重新执行该任务。"

    return {
        "schema_version": 2,
        "workflow_version": 7,
        "report_kind": "task_final",
        "task_id": task_id,
        "carrier": carrier,
        "biztype": biztype,
        "result": result,
        "status": result,
        "terminal_status": result,
        "terminal_step": str(final_output.get("terminal_step") or terminal_step or existing.get("terminal_step") or "workflow"),
        "terminal_reason": terminal_reason or "任务结束，但现有记录没有提供更具体的结束原因。",
        "next_action": next_action or "查看终态步骤的错误详情和关联产物，确认后续处置动作。",
        "finished_at": str(existing.get("finished_at") or final_output.get("finished_at") or finished_at),
        "changed_files": [
            str(item) for item in (final_output.get("changed_files") or existing.get("changed_files") or [])
        ],
        "root_cause": str(existing.get("root_cause") or locate.get("root_cause") or locate.get("analysis") or ""),
        "change_summary": str(existing.get("change_summary") or repair.get("summary") or repair.get("analysis") or ""),
    }


def _read_json_file(path: Path) -> dict[str, Any]:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _write_run_report_if_terminal(
    store: RecordStore,
    run_id: str,
    graph: Any,
    *,
    final_state: dict[str, Any] | None = None,
) -> None:
    """只有图确实进入终态时才写 Run final.json；暂停和等待审批不是终态。"""
    try:
        snapshot = graph.get_state({"configurable": {"thread_id": run_id}})
        interrupted = any(
            bool(getattr(task, "interrupts", ()))
            for task in (getattr(snapshot, "tasks", ()) or ())
        )
        if interrupted or (isinstance(final_state, dict) and final_state.get("__interrupt__")):
            return
    except Exception:
        if isinstance(final_state, dict) and final_state.get("__interrupt__"):
            return
        logger.debug("无法确认工作流 checkpoint 是否处于 interrupt 状态 run_id=%s", run_id, exc_info=True)
    _write_run_report(store, run_id, final_state=final_state)


def _write_run_report(
    store: RecordStore,
    run_id: str,
    final_state: dict[str, Any] | None = None,
    *,
    error: BaseException | None = None,
) -> None:
    """每轮执行无论正常、无任务还是异常退出，都写一份 Run 级终态报告。"""
    now = datetime.now(timezone.utc).isoformat()
    try:
        run_dir = store.run_dir(run_id)
        reports_dir = run_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        steps_path = store.steps_path(run_id)
        payload = _read_json_file(steps_path)
        slots = payload.get("tasks", []) if isinstance(payload, dict) else []
        task_results = []
        for slot in slots if isinstance(slots, list) else []:
            if not isinstance(slot, dict) or not isinstance(slot.get("task_id"), str):
                continue
            task_id = slot["task_id"]
            report_path = store.task_dir(run_id, task_id) / "reports" / "final.json"
            existing_report = _read_json_file(report_path)
            report = _ensure_task_final_report(
                store,
                run_id,
                final_state or {},
                task_id,
                existing_report if isinstance(existing_report, dict) else {},
                finished_at=now,
                error=error,
            )
            _atomic_json(report_path, report)
            task_results.append({
                "task_id": task_id,
                "carrier": str(report.get("carrier") or ""),
                "biztype": str(report.get("biztype") or ""),
                "result": str(report.get("result") or "INCOMPLETE"),
                "terminal_step": str(report.get("terminal_step") or ""),
                "terminal_reason": str(report.get("terminal_reason") or "任务结束，但无法生成更具体的原因。"),
                "next_action": str(report.get("next_action") or "检查该任务最后一个步骤的输出和产物。"),
            })

        run_steps = store.list_steps(run_id)
        failed_steps = [step for step in run_steps if step.status == "failed"]
        cancelled = False
        try:
            cancelled = cancellation_requested(store.root, run_id)
        except Exception:
            logger.debug("读取 Run 取消标记失败 run_id=%s", run_id, exc_info=True)

        if error is not None:
            result = "FAILED"
            terminal = failed_steps[-1] if failed_steps else (run_steps[-1] if run_steps else None)
            reason = f"{type(error).__name__}: {error}".strip()
            terminal_step = terminal.name if terminal else "workflow"
            next_action = "检查工作流异常堆栈、最后一个节点的输入输出和运行产物后重试。"
            failure = {
                "status": "FAILED",
                "error_type": type(error).__name__,
                "message": str(error),
                "failed_at": now,
                "terminal_step": terminal_step,
                "terminal_reason": reason,
            }
            _atomic_json(reports_dir / "run_failure.json", failure)
        elif cancelled:
            result = "CANCELLED"
            terminal = run_steps[-1] if run_steps else None
            terminal_step = terminal.name if terminal else "workflow"
            reason = "收到停止请求，本轮执行已按安全节点策略终止。"
            next_action = "确认停止原因；如仍需修复，请在问题和证据确认后重新发起一轮执行。"
        elif failed_steps:
            terminal = failed_steps[-1]
            result = "FAILED"
            reason = str(getattr(terminal.error, "message", "") or terminal.summary or
                         f"Run 级步骤 {terminal.name} 执行失败，但没有记录具体原因。")
            terminal_step = terminal.name
            next_action = "检查失败节点的错误详情和产物，修复配置或输入后重新执行。"
        elif not task_results:
            result = "NO_TASKS"
            terminal = next((step for step in reversed(run_steps) if step.name == "identify_carrier"),
                            run_steps[-1] if run_steps else None)
            terminal_step = terminal.name if terminal else "workflow"
            reason = "本轮工作流已结束，但没有产生可处理的运输商任务。"
            next_action = "检查日志筛选条件、异常规范化、去重结果和运输商识别输出。"
        else:
            counts: dict[str, int] = {}
            for task_item in task_results:
                outcome = task_item["result"].upper()
                counts[outcome] = counts.get(outcome, 0) + 1
            total = len(task_results)
            successes = counts.get("SUCCESS", 0) + counts.get("SUCCEEDED", 0)
            manuals = counts.get("MANUAL", 0) + counts.get("INSUFFICIENT_EVIDENCE", 0)
            failures = sum(count for key, count in counts.items()
                           if key in {"FAILED", "FAIL", "REJECTED", "INCOMPLETE", "CANCELLED"})
            if failures or manuals:
                result = "PARTIAL_FAILED" if successes else ("MANUAL" if manuals and not failures else "FAILED")
            elif successes == total:
                result = "SUCCESS"
            else:
                result = "INCOMPLETE"
            blocker = next((item for item in task_results if item["result"].upper() not in {"SUCCESS", "SUCCEEDED"}), None)
            terminal_step = str(blocker.get("terminal_step") or "task_collect") if blocker else "task_collect"
            reason = f"本轮共处理 {total} 个任务：成功 {successes} 个，需人工介入 {manuals} 个，失败或未完成 {failures} 个。"
            if blocker and blocker.get("terminal_reason"):
                reason += f" 首个未成功任务：{blocker.get('carrier')} / {blocker.get('biztype')} — {blocker.get('terminal_reason')}"
            next_action = str(blocker.get("next_action") or "检查各任务终态报告，按团队流程完成人工评审与合并。") if blocker else "所有任务均已完成交付准备；按团队流程完成人工评审与合并。"

        report = {
            "report_version": 1,
            "report_kind": "run_final",
            "run_id": run_id,
            "result": result,
            "status": result,
            "terminal_step": terminal_step,
            "terminal_reason": reason,
            "next_action": next_action,
            "finished_at": now,
            "task_count": len(task_results),
            "task_results": task_results,
        }
        _atomic_json(reports_dir / "final.json", report)

        passed = sum(1 for item in task_results if item.get("result") in {"SUCCESS", "SUCCEEDED", "OK"})
        failed_count = len(task_results) - passed
        summary = {
            "run_id": run_id,
            "completed_at": now,
            "task_count": len(task_results),
            "passed_count": passed,
            "failed_count": failed_count,
            "tasks": task_results,
        }
        _atomic_json(reports_dir / "summary.json", summary)
    except Exception:
        logger.exception("写入 Run 级终态报告失败 run_id=%s", run_id)


__all__ = [
    "RUN_TASK",
    "TASK_COLLECT",
    "TASK_DISPATCH",
    "after_dispatch",
    "build_agent_node",
    "build_graph",
    "build_nodes",
    "build_services",
    "build_task_graph",
    "close_checkpoints",
    "collect",
    "continue_task_after_step",
    "dispatch",
    "kept_repair_slot",
    "prepare_run",
    "resume_task_approval",
    "run_workflow",
    "_ensure_task_final_report",
    "_write_run_report",
    "_write_run_report_if_terminal",
]
