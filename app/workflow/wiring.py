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
from app.workflow.definitions import PHASE_COMPOSITIONS
from app.domain.errors import StepFailed
from app.application.audit import StepError, StepRecord
from app.domain.models import utc_now
from app.infrastructure.git.source import sync_source_repo
from app.workflow.runner import bind
from app.workflow.routes import FINALIZE, SOURCE_SYNC
from app.infrastructure.records.store import RecordStore
from app.infrastructure.redaction import public_exception
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


def close_checkpoints() -> None:
    """关闭进程内复用的 checkpoint SQLite 连接。"""
    with _CHECKPOINT_LOCK:
        connections = list(_CHECKPOINT_CONNECTIONS.values())
        _CHECKPOINT_CONNECTIONS.clear()
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
        saver = SqliteSaver(connection)
        saver.setup()
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
            "independent_verify": partial(
                _independent_verify,
                timeout_seconds=config.replay_timeout_seconds,
            )
        },
    )
    return services


def _independent_verify(
    payload: dict[str, Any],
    *,
    timeout_seconds: int = settings.replay_timeout_seconds,
) -> dict[str, Any]:
    """两阶段验证：1) Python 语法检查 2) Response 重放验证（可选）。"""
    import json
    import os
    import py_compile
    import shutil
    from pathlib import Path

    from app.infrastructure.filesystem.safe_cleanup import remove_tree, temporary_directory

    # ========== 阶段 1: Python 语法检查 ==========
    files = [str(item) for item in payload.get("changed_files") or [] if str(item).endswith(".py")]
    workspace = Path(str(payload.get("ai_workspace_path") or ""))
    if not workspace.is_dir():
        return {"passed": False, "mode": "unavailable", "reason": "验证工作区不存在"}
    if not files:
        return {"passed": True, "mode": "syntax_only", "reason": "本轮仅修改非 Python 文件，未执行问题重放"}
    # py_compile 默认把 .pyc 写到源文件旁的 __pycache__，会污染隔离区和后续
    # check_changes 的 diff。编译产物必须落在工作区外的临时目录。
    with temporary_directory("ai-verify-pycache-") as pycache:
        for relative in files:
            target = (workspace / relative).resolve()
            try:
                target.relative_to(workspace.resolve())
            except ValueError:
                return {"passed": False, "mode": "unavailable", "reason": "改动文件超出工作区"}
            if not target.is_file():
                return {"passed": False, "mode": "unavailable", "reason": f"改动文件不存在: {relative}"}
            try:
                py_compile.compile(
                    str(target),
                    cfile=str(Path(pycache) / f"{abs(hash(relative))}.pyc"),
                    doraise=True,
                )
            except py_compile.PyCompileError as error:
                return {"passed": False, "mode": "syntax_only", "reason": f"语法检查失败: {relative}: {error.msg}"}

    # ========== 阶段 2: Response 重放验证（可选）==========
    # response / track_config 既可能在记录顶层，也可能嵌在 req 对象里。
    normalized_log = payload.get("normalized_log")
    if not isinstance(normalized_log, dict):
        return {"passed": True, "mode": "syntax_only", "reason": f"语法检查通过: {len(files)} 个文件；未执行问题重放"}
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
    track_config_data = normalized_log.get("track_config") or req_data.get("track_config")
    if not isinstance(response_data, str):
        response_data = json.dumps(response_data, ensure_ascii=False) if response_data else ""
    if not isinstance(track_config_data, str):
        track_config_data = json.dumps(track_config_data, ensure_ascii=False) if track_config_data else ""
    if not (carrier and req_data and response_data):
        return {"passed": True, "mode": "syntax_only", "reason": f"语法检查通过: {len(files)} 个文件；未执行问题重放"}

    from app.workflow.steps.verify_issue.replay_runner import run_replay

    # 共享树是给模型阅读的脱敏快照：_sanitize_shared_tree 只保留 .py/.json 等
    # 白名单后缀，会删除 py_rules_new.zip 这类运行资产。重放必须在未脱敏的
    # 干净 Git 业务目录上建立基线，不能复用共享树。
    clean_root = Path(str(payload.get("clean_source_path") or "")).resolve()
    if not (clean_root.is_dir() and ((clean_root / "gathers").is_dir() or (clean_root / "spiders").is_dir())):
        clean_root = None
    shared_root = Path(str(payload.get("source_project_path") or "")).resolve()
    if not shared_root.is_dir():
        shared_root = None
    baseline_root = clean_root or shared_root
    # 重放需要 server_dev、framework 和公共库；任务工作区只有目标 carrier。
    # 在基线同盘临时目录建立镜像（优先硬链接），再用 AI 工作区的 carrier 覆盖，
    # 既保证导入完整，也不写回源仓库。
    mirror_parent = baseline_root.parent if baseline_root is not None else workspace
    with temporary_directory("ai-replay-workspace-", parent=mirror_parent) as directory:
        replay_root = Path(directory) / "repo"
        if baseline_root is not None:
            try:
                shutil.copytree(
                    baseline_root, replay_root,
                    copy_function=os.link,
                    ignore=shutil.ignore_patterns(".git"),
                )
            except OSError:
                remove_tree(replay_root)
                shutil.copytree(
                    baseline_root, replay_root,
                    ignore=shutil.ignore_patterns(".git"),
                )
            replay_carrier = WorkspaceManager.find_carrier_dir(replay_root, "gathers", carrier)
            source_carrier = WorkspaceManager.find_carrier_dir(workspace, "gathers", carrier)
            if replay_carrier is None or source_carrier is None:
                return {
                    "passed": False,
                    "mode": "response_replay",
                    "reason": "重放工作区缺少目标 carrier 目录",
                }
            # 目标 carrier 必须与 AI 工作区完全一致（包括删除的文件）。
            if not remove_tree(replay_carrier):
                return {
                    "passed": False,
                    "mode": "response_replay",
                    "reason": "重放工作区 carrier 清理被环境拦截",
                }
            shutil.copytree(source_carrier, replay_carrier, copy_function=shutil.copy2)
        else:
            replay_root = workspace
        gathers_root = replay_root / "gathers"
        if not gathers_root.is_dir():
            return {"passed": False, "mode": "response_replay", "reason": "gathers 目录不存在，无法执行 response 重放"}

        replay_result = run_replay(
            carrier=carrier,
            req_json=json.dumps(req_data, ensure_ascii=False),
            response_json=response_data,
            track_config_json=track_config_data,
            gathers_root=gathers_root,
            timeout_seconds=timeout_seconds,
        )

    if not replay_result.success:
        return {
            "passed": False,
            "mode": "response_replay",
            "reason": f"Response 重放验证失败: {replay_result.error}",
            "replay_exception_type": replay_result.exception_type,
        }

    return {
        "passed": True,
        "mode": "response_replay",
        "reason": f"语法检查 + Response 重放验证通过: {len(files)} 个文件",
        "replay_output": replay_result.output[: settings.replay_output_chars],  # 截取前若干字符
    }


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
    def node(state: dict[str, Any]) -> dict[str, Any]:
        run_id = str(state.get("run_id") or "")
        if not run_id:
            raise RuntimeError("sync_source_repo 缺少 run_id")
        previous = store.load(run_id, SOURCE_SYNC)
        attempt = (previous.attempt if previous else 0) + 1
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
        try:
            source_repo = sync_source_repo(config)
        except Exception as error:
            record.status = "failed"
            record.finished_at = utc_now()
            record.duration_ms = 0
            record.summary = public_exception(error, prefix="源仓库同步失败")
            record.output = {"failure_reason": record.summary}
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
        nodes[phase_name] = _compose_phase(step_names, nodes)
    nodes[SOURCE_SYNC] = _source_sync_node(load_config(), store)
    return nodes


def _compose_phase(
    step_names: tuple[str, ...],
    nodes: dict[str, Callable[..., Any]],
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """顺序执行一个组合阶段；失败、跳过、人工介入或取消后立即停止后续子步骤。"""
    missing = [name for name in step_names if name not in nodes]
    if missing:
        raise PluginError(f"组合阶段缺少步骤插件: {', '.join(missing)}")

    def node(state: dict[str, Any]) -> dict[str, Any]:
        current = dict(state)
        combined_patch: dict[str, Any] = {}
        for name in step_names:
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
        if pause_requested(store.root, run_id):
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
            if kind == "locate":
                incoming = input_model(
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
                    task_id=str(state.get("task_id") or ""),
                    attempt=int(state.get("attempt") or 1),
                    ai_workspace_path=str(state.get("ai_workspace_path") or ""),
                    repair=RepairView.model_validate(repair_payload) if repair_payload else RepairView(),
                    changed_files=[str(item) for item in (state.get("changed_files") or [])],
                    # 复核/回归需要看到"模型到底改了哪几行"。原先这里只塞了 scope/totals
                    # 这类统计数字（甚至取不到就退化成空串），审查环节无法判断改动合理性。
                    diff_summary=_diff_summary(store, str(state["run_id"]), str(state.get("task_id") or ""), state.get("gate_result") or {}),
                    gate_result=dict(state.get("gate_result") or {}),
                    verify_result=dict(state.get("verify_result") or {}),
                    regression_result=dict(state.get("regression_result") or {}),
                    check_passed=bool(state.get("check_passed")),
                    verify_passed=bool(state.get("verify_passed")),
                    regression_passed=bool(state.get("regression_passed")),
                )
                graph_input = {
                    **task,
                    **dump_model(incoming),
                    "repair": repair_payload,
                    "run_id": str(state["run_id"]),
                    "baseline_dir": str(state.get("baseline_dir") or ""),
                    "baseline_manifest": dict(state.get("baseline_manifest") or {}),
                    "framework": str(state.get("framework") or ""),
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
            conclusion = (
                ("FAIL" if view.result == "failed" else None)
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
                        or bool((state.get("evidence") or {}).get("legacy_only"))
                        or bool((state.get("evidence") or {}).get("legacy_dependency"))
                        or str(view.recommended_scope or "") != "carrier"
                        or str(view.category or "") != "carrier"
                        or any(
                            str(item.path or "").replace("\\", "/").startswith("spiders/")
                            for item in view.source_evidence
                        )
                        or any(
                            str(item).replace("\\", "/").startswith("spiders/")
                            for item in view.candidate_files
                        )
                    )
                ) or (kind == "review" and str(view.verdict or "").upper() == "MANUAL")
                else "failed" if failed else "passed"
            ),
        }
        if kind == "locate":
            patch["last_result"] = "manual" if failed else "passed"
            patch["scope_escalation_required"] = bool(final.get("scope_escalation_required")) or bool(view.recommended_scope == "project")
        if kind == "review" and view.change_plan:
            patch["change_plan"] = dict(view.change_plan)
            patch["scope_escalation_required"] = str(view.change_plan.get("scope") or "") == "project"
        if kind == "review":
            # 交付门禁读的是审查结论，不能只留在 review 子字典里。
            patch["review_decision"] = str(view.verdict or "")
        if kind == "repair":
            repair_files = [str(item) for item in (final.get("changed_files") or [])]
            change_plan = dict(final.get("change_plan") or {})
            locate = dict(state.get("locate") or {})
            patch["repair_changed_files"] = repair_files
            patch["changed_files"] = repair_files
            patch["change_plan"] = change_plan
            patch["blocked_reason"] = str(final.get("blocked_reason") or "")
            # 修复 Agent 的输出不能抹掉定位阶段已经确定的共享层升级信号。
            scope_escalation = bool(
                final.get("scope_escalation_required")
                or state.get("scope_escalation_required")
                or str(locate.get("recommended_scope") or "") == "project"
            )
            patch["scope_escalation_required"] = scope_escalation
            if scope_escalation and not change_plan:
                # 不允许共享层改动在没有可审方案时被重试/收尾悄悄吞掉。
                source_evidence = locate.get("source_evidence")
                candidate_files = locate.get("candidate_files")
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

    先编译任务子图，再把它作为节点嵌套进 run 级主图。
    """
    resolved_config = config or load_config()
    resolved_store = store or RecordStore(resolved_config.runtime_dir)
    if checkpointer is None:
        saver = _checkpoint_saver(resolved_config.runtime_dir)
    else:
        saver = checkpointer
    resolved_services = services or build_services(config=resolved_config)
    nodes = build_nodes(resolved_store, resolved_services, runtimes)
    task_graph = build_task_graph(
        nodes,
        workspace_manager=getattr(resolved_services, "workspaces", None),
    )
    return build_state_graph(nodes, task_graph, checkpointer=saver)


__all__ = [
    "build_agent_node",
    "build_graph",
    "build_nodes",
    "build_services",
    "close_checkpoints",
]
