"""图装配。唯一入口：``build_graph``。

步骤插件由 :mod:`app.workflow` 发现，本文件只声明编排组合和图结构。
三个 agent 在各自目录编译，主图只接收编译后的图。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.infrastructure.filesystem.workspace import WorkspaceConfig, WorkspaceManager
from app.infrastructure.git.delivery import GitBranchDelivery, GitDeliveryConfig
from app.infrastructure.es.logs import ESLogAdapter
from app.infrastructure.filesystem.scope import CarrierScopePolicy
from app.infrastructure.config import load_config
from app.infrastructure.eligibility import ExecutionEligibility
from app.workflow.graph import build_graph as build_state_graph
from app.workflow.graph import build_task_graph
from app.workflow.definitions import PHASE_COMPOSITIONS
from app.domain.errors import StepFailed
from app.workflow.runner import bind
from app.workflow.routes import FINALIZE
from app.infrastructure.records.store import RecordStore
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


def build_services() -> WorkflowServices:
    """按当前配置装配外部依赖。缺配置的字段保持空，对应步骤运行时失败。"""
    from app.infrastructure.config import create_es_client, index_days
    config = load_config()
    workspaces = WorkspaceManager(WorkspaceConfig(root_dir=config.workspace_root, project_subdir=config.project_subdir))
    log_source = None
    if config.log_es:
        from elasticsearch7.helpers import scan

        log_source = ESLogAdapter(
            create_es_client(config),
            scan,
            lambda prefix, days: index_days(prefix, days, config.log_tz_offset),
            config.log_tz_offset,
        )
    services = WorkflowServices(
        source_repo=config.source_repo,
        project_subdir=config.project_subdir,
        verify_commands=list(config.verify_commands),
        git_push=config.git_push,
        workspaces=workspaces,
        delivery=GitBranchDelivery(
            GitDeliveryConfig(
                remote_name=config.git_remote,
                push=config.git_push,
                source_repo=config.source_repo,
                workspace_manager=workspaces,
            )
        ),
        eligibility=ExecutionEligibility(config.runtime_dir),
        log_source=log_source,
        ignore_errors=list(config.ignore_errors),
        scope_policy=CarrierScopePolicy(),
        metadata={"independent_verify": _independent_verify},
    )
    return services


def _independent_verify(payload: dict[str, Any]) -> dict[str, Any]:
    """两阶段验证：1) Python 语法检查 2) Response 重放验证（可选）。"""
    import py_compile
    from pathlib import Path

    # ========== 阶段 1: Python 语法检查 ==========
    files = [str(item) for item in payload.get("changed_files") or [] if str(item).endswith(".py")]
    workspace = Path(str(payload.get("ai_workspace_path") or ""))
    if not workspace.is_dir():
        return {"passed": False, "reason": "验证工作区不存在"}
    if not files:
        return {"passed": True, "reason": "本轮仅修改非 Python 文件，无需 Python 编译验证"}
    for relative in files:
        target = (workspace / relative).resolve()
        try:
            target.relative_to(workspace.resolve())
        except ValueError:
            return {"passed": False, "reason": "改动文件超出工作区"}
        if not target.is_file():
            return {"passed": False, "reason": f"改动文件不存在: {relative}"}
        try:
            py_compile.compile(str(target), doraise=True)
        except py_compile.PyCompileError as error:
            return {"passed": False, "reason": f"语法检查失败: {relative}: {error.msg}"}

    # ========== 阶段 2: Response 重放验证（可选）==========
    # 尝试从 payload 中提取 response 和 track_config，有这两个字段才执行重放
    normalized_log = payload.get("normalized_log")
    if normalized_log and normalized_log.get("response") and normalized_log.get("track_config"):
        from app.workflow.steps.verify_issue.replay_runner import run_replay
        import json

        carrier = str(normalized_log.get("carrier", ""))
        req_data = normalized_log.get("req", {})
        response_data = str(normalized_log.get("response", ""))
        track_config_data = normalized_log.get("track_config", {})

        if carrier and req_data and response_data:
            # 找到 gathers 根目录
            gathers_root = workspace / "gathers"
            if not gathers_root.is_dir():
                return {"passed": False, "reason": "gathers 目录不存在，无法执行 response 重放"}

            # 执行重放
            replay_result = run_replay(
                carrier=carrier,
                req_json=json.dumps(req_data, ensure_ascii=False),
                response_json=response_data,
                track_config_json=json.dumps(track_config_data, ensure_ascii=False),
                gathers_root=gathers_root,
                timeout_seconds=30,
            )

            if not replay_result.success:
                return {
                    "passed": False,
                    "reason": f"Response 重放验证失败: {replay_result.error}",
                    "replay_traceback": replay_result.traceback,
                }

            return {
                "passed": True,
                "reason": f"语法检查 + Response 重放验证通过: {len(files)} 个文件",
                "replay_output": replay_result.output[:500],  # 截取前 500 字符
            }

    # 没有 response/track_config，只返回语法检查结果
    return {"passed": True, "reason": f"语法检查通过: {len(files)} 个文件"}


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
    return nodes


def _compose_phase(
    step_names: tuple[str, ...],
    nodes: dict[str, Callable[..., Any]],
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """顺序执行一个组合阶段；失败、人工介入或取消后立即停止后续子步骤。"""
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
            if result not in {"passed", "skipped"}:
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
                    source_project_path=str(state.get("ai_workspace_path") or state.get("source_project_path") or ""),
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
                    manual_continue_authorized=bool(state.get("manual_continue_authorized")),
                )
                # 任务字典平铺回子图输入：子图按 carrier / service_name 取字段。
                graph_input = {
                    **task,
                    **dump_model(incoming),
                    "run_id": str(state["run_id"]),
                    "baseline_manifest": dict(state.get("baseline_manifest") or {}),
                    "baseline_dir": str(state.get("baseline_dir") or ""),
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
            record.error = StepError(code=error_code_of("system_crash"), message=f"Agent 输入构造失败: {error}")
            record.output = {"failure_reason": f"Agent 输入构造失败：{error}"}
            record.finished_at = utc_now()
            record.duration_ms = round((time.perf_counter() - started) * 1000, 2)
            store.save(run_id, record, task_id)
            return {
                "last_result": "failed",
                "retry_reason": f"{spec.name} 输入构造失败：{error}",
                "retry_target": spec.name,
                "terminal_status": "failed",
                "attempt": int(state.get("attempt") or 1),
            }
        record = StepRecord(name=spec.name, title=spec.title, status="running", started_at=utc_now(), attempt=int(state.get("attempt") or 1), input=dump_model(incoming))
        refs: list[ArtifactRef] = []
        try:
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
            record.output = {"failure_reason": f"Agent 执行异常：{error}"}
            record.artifacts = list(refs)
            record.error = StepError(code=error_code_of("system_crash"), message=f"Agent内部运行系统级崩溃: {error}")
            record.finished_at = utc_now()
            record.duration_ms = round((time.perf_counter() - started) * 1000, 2)
            store.save(run_id, record, task_id)
            return {
                "last_result": "failed",
                "retry_reason": f"{spec.name} 执行异常：{error}",
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
            patch["manual_intervention_required"] = failed
            patch["manual_continue_allowed"] = bool(final.get("manual_continue_allowed"))
            patch["scope_escalation_required"] = bool(final.get("scope_escalation_required")) or bool(view.recommended_scope == "project")
            patch["approval_mode"] = ""
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
                can_retry_repair = bool(
                    (str(locate.get("status") or "") == "LOCATED" or bool(state.get("manual_continue_authorized")))
                    and str(locate.get("root_cause") or "").strip()
                    and isinstance(source_evidence, list) and source_evidence
                    and isinstance(candidate_files, list) and candidate_files
                )
                patch.update({
                    "manual_intervention_required": True,
                    "manual_continue_allowed": can_retry_repair,
                    "manual_intervention_reason": (
                        "定位已确认需要修改目标 carrier 之外的共享代码，但修复 Agent 没有生成完整的变更方案。"
                        "请重新尝试生成方案，或结束自动修复后人工处理；系统不会直接写入共享层。"
                    ),
                    "manual_intervention_step": "repair_agent",
                    "manual_resume_step": "repair_agent",
                    "last_result": "manual",
                    "terminal_status": "manual",
                })
            else:
                if scope_escalation:
                    patch["terminal_status"] = "manual"
                patch.update({
                    "manual_intervention_required": False,
                    "manual_continue_allowed": False,
                    "manual_intervention_reason": "",
                    "manual_intervention_step": "",
                    "manual_resume_step": "",
                })
            baseline = final.get("baseline_manifest")
            if isinstance(baseline, dict):
                patch["baseline_manifest"] = {str(key): str(value) for key, value in baseline.items()}
            if final.get("baseline_dir"):
                patch["baseline_dir"] = str(final.get("baseline_dir"))
            # 人工继续授权只允许被当前 repair 调用消费一次。
            patch["manual_continue_authorized"] = False
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
) -> CompiledStateGraph:
    """编译主图。仓库未显式给出时按配置目录新建，不依赖全局单例。

    先编译任务子图，再把它作为节点嵌套进 run 级主图。
    """
    resolved_store = store or RecordStore(load_config().runtime_dir)
    if checkpointer is None:
        runtime_dir = load_config().runtime_dir
        runtime_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(runtime_dir / "workflow-checkpoints.sqlite3"),
            check_same_thread=False,
            timeout=30,
        )
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        saver = SqliteSaver(connection)
        saver.setup()
    else:
        saver = checkpointer
    nodes = build_nodes(resolved_store, services or build_services(), runtimes)
    task_graph = build_task_graph(nodes)
    return build_state_graph(nodes, task_graph, checkpointer=saver)


__all__ = ["build_agent_node", "build_graph", "build_nodes", "build_services"]
