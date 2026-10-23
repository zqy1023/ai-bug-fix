"""图装配。唯一入口：``build_graph``。

步骤插件由 :mod:`app.workflow` 发现，本文件只声明编排组合和图结构。
三个 agent 在各自目录编译，主图只接收编译后的图。
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any
import atexit
import threading

from app.infrastructure.filesystem.workspace import WorkspaceConfig, WorkspaceManager
from app.infrastructure.git.delivery import GitBranchDelivery, GitDeliveryConfig
from app.infrastructure.es.logs import ESLogAdapter
from app.infrastructure.config import RuntimeConfig, load_config
from app.workflow.graph import build_graph as build_state_graph
from app.workflow.graph import build_task_graph
from app.workflow.graph import collect
from app.workflow.retry_context import capture_retry_context
from app.workflow.definitions import PHASE_COMPOSITIONS
from app.domain.errors import StepFailed
from app.application.audit import StepError, StepRecord
from app.domain.models import utc_now
from app.infrastructure.git.source import SourceRepoCancelled, SourceRepoPaused, sync_source_repo
from app.workflow.runner import bind
from app.workflow.routes import FINALIZE, SOURCE_SYNC
from app.infrastructure.records.store import RecordStore
from app.infrastructure.redaction import public_exception
from app.infrastructure.run_control import cancellation_requested, pause_requested
from app.application.services import WorkflowServices
from app.workflow import registry
from app.workflow.plugin_checks import verify_registry
from app.workflow.plugin_loader import load_all
from app.workflow.registry import PluginError
from app.workflow.spec import AgentSpec
from config.settings import settings
import sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph.state import CompiledStateGraph


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
            connection.execute(f"PRAGMA busy_timeout={settings.workflow_checkpoint_busy_timeout_ms}")
            _CHECKPOINT_CONNECTIONS[path] = connection
        saver = _CHECKPOINT_SAVERS.get(path)
        if saver is None:
            saver = SqliteSaver(connection)
            saver.setup()
            _CHECKPOINT_SAVERS[path] = saver
        return saver

def _agent_builder(kind: str) -> Callable[[Any | None], CompiledStateGraph]:
    """只在装配 Agent 节点时导入对应子图。"""
    if kind == "locate":
        from app.agents.locate.graph import build
    elif kind == "repair":
        from app.agents.repair.graph import build
    elif kind == "review":
        from app.agents.review.graph import build
    else:
        raise PluginError(f"未注册的 Agent 子图: {kind}")
    return build


def build_services(
    config: RuntimeConfig | None = None,
    source_repo: Path | None = None,
) -> WorkflowServices:
    """按当前配置装配外部依赖。缺配置的字段保持空，对应步骤运行时失败。

    源仓库同步由执行入口完成；只读的图装配（checkpoint 查询等）不触发网络操作。
    """
    from app.infrastructure.config import create_es_client, index_days
    from app.infrastructure.git.source import git_auth_env
    from app.workflow.steps.run_regression.request_runner import run_request_regression
    config = config or load_config()
    resolved_source = source_repo if source_repo is not None else config.source_repo
    workspaces = WorkspaceManager(WorkspaceConfig(root_dir=config.workspace_root, project_subdir=config.project_subdir))
    log_source = None
    if config.log_es:
        from elasticsearch7.helpers import scan

        log_source = ESLogAdapter(
            create_es_client(config),
            scan,
            lambda prefix, days: index_days(prefix, days, config.log_tz_offset),
            config.log_tz_offset,
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
                _independent_verify,
                timeout_seconds=config.replay_timeout_seconds,
                replay_python=config.replay_python,
            )
        },
    )
    return services


def _independent_verify(
    payload: dict[str, Any],
    *,
    timeout_seconds: int = settings.replay_timeout_seconds,
    replay_python: str = settings.replay_python,
) -> dict[str, Any]:
    """固定原始正文验证解析行为；语法检查由前置步骤独立负责。"""
    import hashlib
    import json
    import shutil
    from pathlib import Path

    from app.infrastructure.filesystem.safe_cleanup import remove_tree, temporary_directory

    from app.workflow.steps.verify_issue.assessment import assessment, changed_functions, parser_configuration_changes

    files = [str(item) for item in payload.get("changed_files") or []]
    workspace = Path(str(payload.get("ai_workspace_path") or ""))
    if not workspace.is_dir():
        return {"passed": False, "status": "unverified", "mode": "unavailable", "reason": "验证工作区不存在"}
    if not files:
        return {"passed": False, "status": "unverified", "mode": "unavailable", "reason": "没有可核对的修改文件"}
    # response / track_config 既可能在记录顶层，也可能嵌在 req 对象里。
    normalized_log = payload.get("normalized_log")
    if not isinstance(normalized_log, dict):
        return {"passed": False, "status": "unverified", "mode": "response_replay", "reason": "缺少原始问题记录，无法执行业务验证"}
    carrier = str(normalized_log.get("carrier") or payload.get("carrier") or "")
    req_data = normalized_log.get("req", {})
    if isinstance(req_data, str):
        try:
            req_data = json.loads(req_data)
        except json.JSONDecodeError:
            req_data = {}
    if not isinstance(req_data, dict):
        req_data = {}
    response_data = normalized_log.get("response") or req_data.get("response")
    from app.workflow.steps.verify_issue.replay_model import extract_track_config

    track_config_data = extract_track_config(normalized_log)
    if not isinstance(response_data, str):
        response_data = json.dumps(response_data, ensure_ascii=False) if response_data else ""
    if not isinstance(track_config_data, str):
        track_config_data = json.dumps(track_config_data, ensure_ascii=False) if track_config_data else ""
    original_error = str(normalized_log.get("error") or normalized_log.get("raw_error") or "")
    from app.workflow.steps.verify_issue.replay_model import request_numbers
    from app.workflow.steps.verify_issue.replay_runner import run_replay
    from app.workflow.steps.verify_issue.semantic_compare import ReplayOutcome, compare

    if not (carrier and req_data and request_numbers(req_data)):
        return {"passed": False, "status": "unverified", "mode": "response_replay", "reason": "缺少运输商或有效原始请求，未执行业务验证"}
    if not original_error.strip():
        return {"passed": False, "status": "unverified", "mode": "response_replay", "reason": "缺少原始异常，无法判断基线是否复现问题"}
    if not response_data and not payload.get("allow_live_replay", False):
        return {"passed": False, "status": "unverified", "mode": "response_replay",
                "replay_input_mode": "missing_response", "replay_verdict": "missing_evidence",
                "reason": "没有录制响应，未发起不稳定的在线请求；需补充原始响应或人工确认，语法检查不能代替业务验证"}
    from app.workflow.steps.verify_issue.replay_runner import _resolve_interpreter
    try:
        _resolve_interpreter(replay_python)
    except FileNotFoundError as error:
        return {"passed": False, "status": "unverified", "mode": "response_replay",
                "replay_input_mode": "recorded" if response_data else "live", "replay_verdict": "environment",
                "reason": str(error), "replay_exception_type": "FileNotFoundError"}

    # 共享树是给模型阅读的脱敏快照：_sanitize_shared_tree 只保留 .py/.json 等
    # 白名单后缀，会删除 py_rules_new.zip 这类运行资产。重放必须在未脱敏的
    # 干净 Git 业务目录上建立基线，不能复用共享树。
    baseline_root = None
    for key in ("clean_source_path", "source_project_path"):
        configured = str(payload.get(key) or "").strip()
        if not configured:
            continue
        candidate = Path(configured).resolve()
        if candidate.is_dir() and any((candidate / name).is_dir() for name in ("gathers", "spiders")):
            baseline_root = candidate
            break
    if baseline_root is None:
        return {
            "passed": False, "status": "unverified", "mode": "response_replay",
            "reason": "重放源目录不可用，无法建立独立镜像，业务修复未验证",
        }
    baseline_dir = Path(str(payload.get("baseline_dir") or ""))
    if not payload.get("baseline_dir") or not baseline_dir.is_dir():
        return {"passed": False, "status": "unverified", "mode": "response_replay",
                "reason": "缺少本次修复前源码快照，不允许把当前源码猜作基线"}
    functions = changed_functions(workspace, baseline_dir, files)
    # 重放需要 server_dev、framework 和公共库；任务工作区只有目标 carrier。
    # 两侧使用独立文件副本，避免业务运行写文件污染源仓库或另一侧重放。
    with temporary_directory("ai-replay-workspace-", parent=baseline_root.parent) as directory:
        replay_root = Path(directory) / "repo"
        shutil.copytree(baseline_root, replay_root, ignore=shutil.ignore_patterns(".git"))
        gathers_root = replay_root / "gathers"

        # 基线重放：装入修复前的 carrier 源码快照，用未修复的代码跑同一输入。
        if not _install_baseline_carrier(replay_root, str(payload.get("baseline_dir") or ""), carrier):
            return {"passed": False, "status": "unverified", "mode": "response_replay", "reason": "修复前源码快照不可用，无法建立基线"}
        fixed_root = Path(directory) / "fixed"
        shutil.copytree(replay_root, fixed_root)
        baseline_result = run_replay(
            carrier=carrier,
            req_json=json.dumps(req_data, ensure_ascii=False),
            response_json=response_data,
            track_config_json=track_config_data,
            gathers_root=gathers_root,
            timeout_seconds=timeout_seconds,
            python_executable=replay_python,
        )

        # 修复后重放：把 AI 工作区的 carrier 覆盖进重放工作区，跑同一输入。
        for framework in ("gathers", "spiders"):
            source_carrier = WorkspaceManager.find_carrier_dir(workspace, framework, carrier)
            replay_carrier = WorkspaceManager.find_carrier_dir(fixed_root, framework, carrier)
            if source_carrier is None:
                continue
            destination = replay_carrier or fixed_root / framework / source_carrier.name
            if destination.exists() and not remove_tree(destination):
                return {"passed": False, "status": "unverified", "mode": "response_replay", "reason": "重放工作区 carrier 清理失败"}
            shutil.copytree(source_carrier, destination, copy_function=shutil.copy2)
        fixed_result = run_replay(
            carrier=carrier,
            req_json=json.dumps(req_data, ensure_ascii=False),
            response_json=response_data,
            track_config_json=track_config_data,
            gathers_root=fixed_root / "gathers",
            timeout_seconds=timeout_seconds,
            python_executable=replay_python,
        )

    baseline_outcome = (
        ReplayOutcome.from_result(baseline_result.result)
        if baseline_result.success
        else ReplayOutcome.from_failure(baseline_result.error, baseline_result.exception_type)
    )
    fixed_outcome = (
        ReplayOutcome.from_result(fixed_result.result)
        if fixed_result.success
        else ReplayOutcome.from_failure(fixed_result.error, fixed_result.exception_type)
    )
    comparison = compare(
        baseline=baseline_outcome, fixed=fixed_outcome, original_error=original_error,
        live=not bool(response_data), expected_numbers=request_numbers(req_data),
        captured_response=response_data,
    )
    verification = assessment(
        verdict=comparison.verdict, passed=comparison.passed, functions=functions,
        original_error=original_error,
        response_sha256=hashlib.sha256(response_data.encode("utf-8")).hexdigest(),
        comparison=comparison.details,
        parser_configuration_changes=parser_configuration_changes(workspace, baseline_dir, files),
    )
    passed = comparison.passed
    reason = comparison.reason
    # 全量原文进入脱敏后的验证产物；步骤投影只负责摘要。
    report: dict[str, Any] = {
        "passed": passed,
        "status": "verified" if passed else ("failed" if comparison.verified and not comparison.passed else "unverified"),
        "mode": "response_replay",
        "reason": reason,
        "verification": verification,
        "replay_recipe": {
            "description": "在相同业务依赖根目录重建 input.json 并运行平台脚本；历史临时路径已清理，不能直接复制 command 重跑",
            "input": {"carrier": carrier, "req": req_data, "response": response_data,
                      "track_config": json.loads(track_config_data or "{}")},
            "entry_script": baseline_result.command[2] if baseline_result.command else "",
            "model_builder": Path(__file__).with_name("steps").joinpath("verify_issue/replay_model.py").read_text(encoding="utf-8"),
            "runtime_adapter": Path(__file__).with_name("steps").joinpath("verify_issue/replay_runtime.py").read_text(encoding="utf-8"),
            "business_observer": Path(__file__).with_name("steps").joinpath("verify_issue/business_observation.py").read_text(encoding="utf-8"),
        },
        "replay_verdict": comparison.verdict,
        "replay_input_mode": "live" if not response_data else "recorded",
        "replay_comparison": comparison.details,
        "replay_baseline_output": baseline_result.output,
        "replay_output": fixed_result.output,
        "replay_full_output": True,
        "replay_baseline_stdout": getattr(baseline_result, "stdout", ""),
        "replay_baseline_stderr": getattr(baseline_result, "stderr", ""),
        "replay_baseline_error": baseline_result.error,
        "replay_baseline_exception_type": baseline_result.exception_type,
        "replay_baseline_traceback": baseline_result.traceback,
        "replay_baseline_returncode": getattr(baseline_result, "returncode", None),
        "replay_stdout": getattr(fixed_result, "stdout", ""),
        "replay_stderr": getattr(fixed_result, "stderr", ""),
        "replay_baseline_command": getattr(baseline_result, "command", []),
        "replay_command": getattr(fixed_result, "command", []),
        "replay_returncode": getattr(fixed_result, "returncode", None),
        "replay_error": fixed_result.error,
        "replay_exception_type": fixed_result.exception_type,
        "replay_traceback": fixed_result.traceback,
    }
    return report




def _install_baseline_carrier(replay_root: Path, baseline_dir: str, carrier: str) -> bool:
    """把修复前的 carrier 源码基线装进重放工作区。

    基线快照必须来自本次修改前的实际源码；没有快照就返回 False，
    不把当前源仓库的状态猜作修复前代码。
    """
    from app.infrastructure.filesystem.safe_cleanup import remove_tree

    import shutil

    if not baseline_dir.strip():
        return False
    root = Path(baseline_dir).resolve()
    if not root.is_dir():
        return False
    installed = False
    for framework in ("gathers", "spiders"):
        source = WorkspaceManager.find_carrier_dir(root, framework, carrier)
        if source is None:
            continue
        target = WorkspaceManager.find_carrier_dir(replay_root, framework, carrier)
        if target is None:
            target = replay_root / framework / source.name
        if source == target.resolve() or source.is_relative_to(target.resolve()) or target.resolve().is_relative_to(source):
            return False
        if target.exists() and not remove_tree(target):
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, copy_function=shutil.copy2)
        installed = True
    return installed


_MAX_DIFF_SUMMARY_CHARS = settings.max_diff_summary_chars


def _full_diff(store: RecordStore, run_id: str, task_id: str) -> str:
    """按引用取回完整代码差异。审查必须看到完整差异，不能只看摘要。"""
    record = store.load(run_id, "check_changes", task_id)
    if record is None:
        return ""
    refs = [ref for ref in record.artifacts if ref.name == "diff"]
    if not refs:
        return ""
    payload = store.read_ref(run_id, refs[0], task_id)
    return str(payload.get("diff") or "") if isinstance(payload, dict) else ""


def _diff_summary(store: RecordStore, run_id: str, task_id: str, gate_result: dict[str, Any]) -> str:
    """把门禁算出的代码差异整理成给复核/回归子图看的摘要。

    原先这里 ``json.dumps(scope or totals or {})``：门禁没产出 scope 时退化成空对象，
    再否则只有几个计数——复核环节拿到的"改动摘要"里根本没有代码。差异本体现在在
    ``artifacts/diff.json`` 里，这里按引用取全文，只在超长时截断，不降级成计数。
    """
    text = _full_diff(store, run_id, task_id)
    totals = dict(gate_result.get("totals") or {})
    header = (
        f"共 {totals.get('files', 0)} 个文件，"
        f"新增 {totals.get('added', 0)} 行，删除 {totals.get('removed', 0)} 行"
    )
    if not text:
        return header + "；无基线可比对，未产出逐行差异。"
    body = text if len(text) <= _MAX_DIFF_SUMMARY_CHARS else f"{text[:_MAX_DIFF_SUMMARY_CHARS]}…(已截断)"
    return f"{header}\n{body}"


def build_agent_node(
    spec: AgentSpec,
    store: RecordStore,
    runtime: Any = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """单独构建一个 agent 节点。agent 单步调试走这里，不装配整张图。"""
    builder = _agent_builder(spec.kind)
    return _agent_node(spec, builder(runtime), store)


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

        try:
            control_requested()
            source_repo = sync_source_repo(
                config,
                cancel_requested=control_requested,
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
        nodes[agent.name] = build_agent_node(agent, store, overrides.get(agent.kind))

    # 图上按业务阶段调度；阶段内部仍逐个调用原始插件节点，因此原有步骤记录、
    # artifact、单步调试入口和失败结果语义均保持不变。
    for phase_name, step_names in PHASE_COMPOSITIONS.items():
        nodes[phase_name] = _compose_phase(step_names, nodes, store=store)
    nodes[SOURCE_SYNC] = _source_sync_node(config or load_config(), store)
    return nodes


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
        selected = step_names[step_names.index(after) + 1:] if after in step_names else step_names
        if after in step_names and str(state.get("last_result") or "passed") != "passed":
            return {"continuation": {}}
        for name in selected:
            saved = store.load(str(state["run_id"]), name, str(state.get("task_id") or "")) if store and after in step_names else None
            if saved and saved.status == "succeeded" and saved.started_at >= str(continuation.get("started_at") or "~"):
                spec = registry.resolve(name)
                patch = {**spec.patch(saved.output), "last_result": "passed"}
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


def _agent_node(spec: AgentSpec, graph: CompiledStateGraph, store: RecordStore) -> Callable[..., Any]:
    """子图作为主图节点。记录只写五字段加结果。"""
    import time

    from app.agents.repair.models import RepairView
    from app.domain.models import utc_now
    from app.infrastructure.run_control import cancellation_requested, pause_requested
    from langgraph.types import interrupt
    from app.application.audit import (
        ArtifactRef,
        StepError,
        StepRecord,
        dump_model,
        error_code_of,
        map_outcome,
        summary_of,
    )

    kind = spec.kind
    view_model = spec.output_model
    input_model = spec.input_model

    def node(state: dict[str, Any]) -> dict[str, Any]:
        run_id = str(state.get("run_id") or "")
        task_id = str(state.get("task_id") or "")
        if pause_requested(store.root, run_id) and not state.get("_single_step"):
            waiting = StepRecord(
                name=spec.name, title=spec.title, status="waiting",
                started_at=utc_now(), attempt=int(state.get("attempt") or 1),
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
                from app.workflow.retry_context import stage_retry_evidence
                state = {**state, "retry_context": stage_retry_evidence(store, state)}
            if kind == "locate":
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
                )
                graph_input = dump_model(incoming)
            elif kind == "repair":
                evidence = dict(state.get("evidence") or {})
                if not str(evidence.get("carrier_dir") or "") or not list(evidence.get("candidates") or []):
                    raise StepFailed(f"{spec.name} 缺少已准备的源码证据：没有 carrier 目录或候选文件")
                incoming = input_model(
                    retry_context=dict(state.get("retry_context") or {}),
                    task_id=str(state.get("task_id") or ""),
                    ai_workspace_path=str(state.get("ai_workspace_path") or ""),
                    attempt=int(state.get("attempt") or 1),
                    retry_reason=str(state.get("retry_reason") or ""),
                    evidence=evidence,
                    locate=dict(state.get("locate") or {}),
                )
                # 任务字典平铺回子图输入：子图按 carrier / service_name 取字段。
                graph_input = {
                    **task,
                    **dump_model(incoming),
                    "run_id": str(state["run_id"]),
                    "baseline_manifest": dict(state.get("baseline_manifest") or {}),
                    "baseline_dir": str(state.get("baseline_dir") or ""),
                    "source_project_path": str(state.get("source_project_path") or ""),
                }
            else:
                repair = state.get("repair")
                repair_payload = repair if isinstance(repair, dict) else {}
                incoming = input_model(
                    retry_context=dict(state.get("retry_context") or {}),
                    task_id=str(state.get("task_id") or ""),
                    attempt=int(state.get("attempt") or 1),
                    ai_workspace_path=str(state.get("ai_workspace_path") or ""),
                    validation_summary=dict(state.get("validation_summary") or {}),
                    repair=RepairView.model_validate(repair_payload) if repair_payload else RepairView(),
                    changed_files=[str(item) for item in (state.get("changed_files") or [])],
                    # 复核/回归需要看到"模型到底改了哪几行"。原先这里只塞了 scope/totals
                    # 这类统计数字（甚至取不到就退化成空串），审查环节无法判断改动合理性。
                    diff_summary=_diff_summary(store, str(state["run_id"]), str(state.get("task_id") or ""), state.get("gate_result") or {}),
                    gate_result=dict(state.get("gate_result") or {}),
                    verify_result=dict(state.get("verify_result") or {}),
                    regression_result=dict(state.get("regression_result") or {}),
                    check_passed=bool(state.get("check_passed")),
                    baseline_dir=str(state.get("baseline_dir") or ""),
                    syntax_passed=bool(state.get("syntax_passed")),
                    business_verification_status=str(state.get("business_verification_status") or "unknown"),
                    verify_passed=bool(state.get("verify_passed")),
                    regression_passed=bool(state.get("regression_passed")),
                )
                graph_input = {
                    **task,
                    **dump_model(incoming),
                    "repair": repair_payload,
                    "problem": dict(state.get("problem") or task.get("problem") or {}),
                    "run_id": str(state["run_id"]),
                    "baseline_dir": str(state.get("baseline_dir") or ""),
                    "baseline_manifest": dict(state.get("baseline_manifest") or {}),
                    "framework": str(state.get("framework") or ""),
                    "source_project_path": str(state.get("source_project_path") or ""),
                }
    
        except Exception as error:
            record = StepRecord(
                name=spec.name, title=spec.title, status="failed", started_at=utc_now(),
                attempt=int(state.get("attempt") or 1), input={"failure_stage": "input_validation"},
            )
            record.error = StepError(
                code=error_code_of("system_crash"),
                message=public_exception(error, prefix="Agent 输入构造失败"),
            )
            record.output = {
                "failure_reason": public_exception(error, prefix="Agent 输入构造失败")
            }
            record.finished_at = utc_now()
            record.duration_ms = round((time.perf_counter() - started) * 1000, 2)
            store.save(run_id, record, task_id)
            return {
                "last_result": "failed",
                "retry_reason": public_exception(error, prefix=f"{spec.name} 输入构造失败"),
                "retry_target": spec.name,
                "terminal_status": "failed",
                "attempt": int(state.get("attempt") or 1),
            }
        record = StepRecord(name=spec.name, title=spec.title, status="running", started_at=utc_now(), attempt=int(state.get("attempt") or 1), input=dump_model(incoming))
        refs: list[ArtifactRef] = []
        try:
            # Agent 调用通常持续数分钟：先写入 running 记录，让节点轨和步骤详情实时反映执行中。
            store.save(
                run_id, record, task_id,
                input_view=spec.input_record, output_view=spec.output_record,
            )
            final = graph.invoke(graph_input)
            record.duration_ms = round((time.perf_counter() - started) * 1000, 2)
            from app.infrastructure.records.policy import debug_artifacts
            if debug_artifacts(store.run_dir(run_id)):
                refs.append(ArtifactRef(
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
                ))
            # 只取 final 已有的字段构造 view；缺失字段让 model_validate 报错而不是尝试无参实例化（部分模型有必需字段）。
            view = view_model.model_validate({key: final[key] for key in view_model.model_fields if key in final})

            if kind == "repair":
                is_passed = bool(final.get("passed")) or str(final.get("result") or "").lower() == "passed"
                view = view.model_copy(update={"passed": is_passed, "result": "passed" if is_passed else "failed"})
            elif kind == "locate":
                is_passed = str(final.get("status") or "") == "LOCATED" and bool(final.get("passed"))
                view = view.model_copy(update={"passed": is_passed})
            else:
                raw_verdict = str(final.get("verdict") or "").upper()
                is_passed = bool(final.get("passed")) or raw_verdict == "PASS"
                verdict = "MANUAL" if raw_verdict == "MANUAL" else ("PASS" if is_passed else "FAIL")
                view = view.model_copy(update={"passed": is_passed, "verdict": verdict})
            # agent 跑完了就是执行成功，结论进 result：判定失败不等于节点执行异常。
            refs.append(ArtifactRef(
                name="validated_output", path=store.write_artifact(
                    run_id, f"agents/{spec.name}.validated.json", dump_model(view), task_id,
                ), task_id=task_id,
            ))
            conclusion = (
                ("MANUAL" if view.result == "failed" and view.change_plan is not None and view.change_plan.scope == "manual"
                 else "FAIL" if view.result == "failed" else None)
                if kind == "repair"
                else str(view.status)
                if kind == "locate"
                else str(view.verdict)
            )
            status, business = map_outcome(kind="ok", business=conclusion)
            record.status = status
            record.result = business
            record.output = dump_model(view)
            record.summary = summary_of(record.output) or (str(view.analysis) if kind == "repair" else "")
            audit = final.get("_audit") if isinstance(final, dict) else None
            if isinstance(audit, dict) and audit:
                refs.append(ArtifactRef(
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
                ))
            record.artifacts = list(refs)
        except Exception as error:
            # Agent 的运行异常必须回写为任务局部失败状态，由 retry_prepare 有界重试；
            # 不能把异常抛出 LangGraph 并中止整个 run 的后续 carrier 任务。
            record.status = "failed"
            record.result = None
            record.output = {
                "failure_reason": public_exception(error, prefix="Agent 执行异常")
            }
            record.artifacts = list(refs)
            record.error = StepError(
                code=error_code_of("system_crash"),
                message=public_exception(error, prefix="Agent 内部运行系统级崩溃"),
            )
            record.finished_at = utc_now()
            record.duration_ms = round((time.perf_counter() - started) * 1000, 2)
            store.save(run_id, record, task_id)
            return {
                "last_result": "failed",
                "retry_reason": public_exception(error, prefix=f"{spec.name} 执行异常"),
                "retry_target": spec.name,
                "terminal_status": "failed",
                "attempt": int(state.get("attempt") or 1),
            }
        record.finished_at = utc_now()
        store.save(
            run_id,
            record,
            task_id,
            input_view=spec.input_record,
            output_view=spec.output_record,
        )
        if kind == "repair":
            failed = view.result == "failed"
        elif kind == "locate":
            failed = not view.passed
        else:
            failed = view.verdict == "FAIL"
        if kind == "repair":
            reason = view.summary or view.analysis
        elif kind == "review":
            reason = "\n".join(filter(None, [
                view.summary,
                f"发现项: {', '.join(view.findings)}" if view.findings else "",
                f"要求: {', '.join(view.required_changes)}" if view.required_changes else "",
            ]))
        else:
            reason = view.summary
        patch: dict[str, Any] = {
            kind: view.model_dump(),
            "last_result": "failed" if failed else "passed",
            "next_step": spec.next_step,
            "retry_reason": reason if failed else "",
            "retry_target": "",
            "attempt": int(state.get("attempt") or 1),
            "terminal_status": (
                "manual"
                if (
                    kind == "locate"
                    and (
                        failed
                        or str(view.recommended_scope or "") != "carrier"
                        or str(view.category or "") != "carrier"

                    )
                ) or (kind == "review" and str(view.verdict or "").upper() == "MANUAL")
                else "failed" if failed else "passed"
            ),
        }
        if kind == "locate":
            patch["last_result"] = "manual" if failed else "passed"
            patch["scope_escalation_required"] = bool(final.get("scope_escalation_required")) or bool(view.recommended_scope == "project")
            if not failed:
                # 根因 Agent 与验收方案一次输出；工作流独立校验后，在任何 repair 调用前冻结。
                try:
                    from app.workflow.validation import freeze_acceptance_contract
                    frozen_contract = freeze_acceptance_contract(state, patch["locate"])
                    contract_path = store.write_artifact(
                        run_id, "acceptance/contract.json", frozen_contract, task_id,
                    )
                    refs.append(ArtifactRef(
                        name="acceptance_contract",
                        path=contract_path,
                        task_id=task_id,
                    ))
                    record.artifacts = list(refs)
                    store.save(
                        run_id, record, task_id,
                        input_view=spec.input_record, output_view=spec.output_record,
                    )
                    patch.update(frozen_contract, acceptance_contract_artifact=contract_path)
                except (TypeError, ValueError) as error:
                    locate_payload = dict(patch["locate"])
                    locate_payload.update({
                        "status": "MANUAL",
                        "recommended_scope": "manual",
                        "passed": False,
                        "summary": (str(locate_payload.get("summary") or "") + f"；验收契约冻结失败：{error}").strip("；"),
                    })
                    patch.update({
                        "locate": locate_payload,
                        "last_result": "manual",
                        "terminal_status": "manual",
                        "retry_target": "",
                        "retry_reason": f"验收契约未能在修改前冻结：{error}",
                    })
        if kind == "review":
            patch["review_decision"] = str(view.verdict)
            patch["review_contract_error"] = view.contract_error
            # 此节点只保存审查意见，交付和恢复由 decide_validation 决定。
            patch["terminal_status"] = "passed"
        if failed and kind == "repair":
            patch["retry_target"] = "repair_agent"
        elif failed and kind == "review":
            patch["retry_target"] = "validate_changes" if (final.get("_audit") or {}).get("blocked_by") == "stale_evidence" else "repair_agent"
        if kind == "repair":
            repair_files = [str(item) for item in (final.get("changed_files") or [])]
            change_plan = view.change_plan.model_dump(mode="json") if view.change_plan is not None else {}
            locate = dict(state.get("locate") or {})
            patch["repair_changed_files"] = repair_files
            patch["changed_files"] = repair_files
            patch["change_plan"] = change_plan
            patch["blocked_reason"] = str(final.get("blocked_reason") or "")
            if failed and str(change_plan.get("scope") or "") == "manual":
                # 显式人工方案缺少自动继续条件，重复调用模型不会补齐证据。
                patch.update(last_result="manual", terminal_status="manual", retry_target="")
            # 修复 Agent 的输出不能抹掉定位阶段已经确定的共享层升级信号。
            scope_escalation = bool(
                final.get("scope_escalation_required")
                or state.get("scope_escalation_required")
                or str(locate.get("recommended_scope") or "") == "project"
            )
            patch["scope_escalation_required"] = scope_escalation
            if scope_escalation and not change_plan:
                # 不允许共享层改动在没有可审方案时被重试/收尾悄悄吞掉。
                patch.update({
                    "last_result": "manual",
                    "terminal_status": "manual",
                })
            else:
                if scope_escalation:
                    patch["terminal_status"] = "manual"
            baseline = final.get("baseline_manifest")
            if isinstance(baseline, dict):
                patch["baseline_manifest"] = {str(key): str(value) for key, value in baseline.items()}
            if final.get("baseline_dir"):
                patch["baseline_dir"] = str(final.get("baseline_dir"))
        if cancellation_requested(store.root, run_id):
            patch.update({
                "last_result": "cancelled",
                "terminal_status": "CANCELLED",
                "next_step": FINALIZE,
            })
        return patch

    node.__name__ = spec.name
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
    nodes = build_nodes(resolved_store, resolved_services, runtimes, config=resolved_config)
    detached_graph = build_task_graph(
        nodes,
        workspace_manager=getattr(resolved_services, "workspaces", None),
        checkpointer=saver,
        retry_context_builder=lambda state: capture_retry_context(state, resolved_store),
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
        pointer = resolved_store.task_dir(str(state["run_id"]), str(state["task_id"])) / "workflow/checkpoint.json"
        if pointer.is_file():
            saved = resolved_store.read_artifact(str(state["run_id"]), "workflow/checkpoint.json", str(state["task_id"]))
            if saved.get("metadata", {}).get("continuation") and saved.get("metadata", {}).get("task_cycle") == cycle:
                config = saved
        resolved_store.write_artifact(str(state["run_id"]), "workflow/checkpoint.json",
                                      config, str(state["task_id"]))
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

    # Keep the node name stable so legacy parent cursors can advance after migration.
    graph = build_state_graph(
        nodes, detached_graph, checkpointer=saver,
        task_collector=collect_task, task_runner=run_task,
    )
    return graph


def resume_task_approval(
    store: RecordStore, run_id: str, task_id: str, payload: dict[str, Any],
    config: RuntimeConfig, services: WorkflowServices,
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
        retry_context_builder=lambda state: capture_retry_context(state, store),
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
        from app.workflow.steps.claim_repair_slot.node import renew_claim
        renew_claim(store.root, run_id=run_id, task_id=task_id)
    result = invoke_task(graph, Command(resume=payload), checkpoint_config)
    if not result.get("__interrupt__"):
        store.complete_task(run_id, task_id)
    return True


def continue_task_after_step(
    store: RecordStore, run_id: str, task_id: str, step_name: str, payload: dict[str, Any],
    config: RuntimeConfig, services: WorkflowServices,
) -> dict[str, Any]:
    """Continue the selected task through authoritative routers without rerunning the step."""
    from app.workflow.validation import assert_workflow_version
    assert_workflow_version(store.load_run(run_id))
    import uuid
    import re
    from app.application.step_runner import state_from_records
    from app.workflow.task_execution import task_config, invoke_task

    state = {**state_from_records(store, run_id, task_id), **payload}
    record = store.load(run_id, step_name, task_id)
    state["attempt"] = max(int(state.get("attempt") or 1), int(record.attempt if record else 1))
    state["automatic_attempt"] = 1
    state["terminal_status"] = str(payload.get("terminal_status") or "")
    state["task_summary"] = {}
    state["max_attempts"] = int(store.load_run(run_id).get("max_attempts") or config.max_attempts)
    nodes = build_nodes(store, services, config=config)
    owner = next((name for name, members in PHASE_COMPOSITIONS.items() if step_name in members), step_name)
    partial = owner != step_name
    if partial:
        state["continuation"] = {"after_step": step_name, "started_at": utc_now()}
    saver = _checkpoint_saver(config.runtime_dir)
    graph = build_task_graph(nodes, workspace_manager=getattr(services, "workspaces", None),
                             checkpointer=saver, entry_node=owner if partial else None,
                             retry_context_builder=lambda value: capture_retry_context(value, store))
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


__all__ = [
    "build_agent_node",
    "build_graph",
    "build_nodes",
    "build_services",
    "close_checkpoints",
    "resume_task_approval",
]
