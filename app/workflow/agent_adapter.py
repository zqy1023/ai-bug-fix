"""Agent 子图输入构造、输出校验、审计记录及正式状态交接。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from app.domain.errors import StepFailed
from app.infrastructure.records.store import RecordStore
from app.infrastructure.redaction import public_exception
from app.workflow.registry import PluginError
from app.workflow.routes import FINALIZE
from app.workflow.spec import AgentSpec
from config.settings import settings

_MAX_DIFF_SUMMARY_CHARS = settings.max_diff_summary_chars


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


def _diff_summary(
    store: RecordStore, run_id: str, task_id: str, gate_result: dict[str, Any]
) -> str:
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
    body = (
        text
        if len(text) <= _MAX_DIFF_SUMMARY_CHARS
        else f"{text[:_MAX_DIFF_SUMMARY_CHARS]}…(已截断)"
    )
    return f"{header}\n{body}"


def build_agent_node(
    spec: AgentSpec,
    store: RecordStore,
    runtime: Any = None,
    services: Any = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """单独构建一个 agent 节点。agent 单步调试走这里，不装配整张图。"""
    builder = _agent_builder(spec.kind)
    model_node = _agent_node(spec, builder(runtime), store)
    if spec.kind == "review":
        from app.agents.review.pipeline import build_review_pipeline
        if services is None:
            from app.workflow.wiring import build_services
            services = build_services()
        return build_review_pipeline(store, services, model_node)
    return model_node


def _agent_node(
    spec: AgentSpec, graph: CompiledStateGraph, store: RecordStore
) -> Callable[..., Any]:
    """子图作为主图节点。记录只写五字段加结果。"""
    import time

    from langgraph.types import interrupt

    from app.agents.repair.models import RepairView
    from app.application.audit import (
        ArtifactRef,
        StepError,
        StepRecord,
        dump_model,
        error_code_of,
        map_outcome,
        summary_of,
    )
    from app.domain.models import utc_now
    from app.infrastructure.run_control import cancellation_requested, pause_requested

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
                if not str(evidence.get("carrier_dir") or "") or not list(
                    evidence.get("candidates") or []
                ):
                    raise StepFailed(
                        f"{spec.name} 缺少已准备的源码证据：没有 carrier 目录或候选文件"
                    )
                from app.workflow.acceptance import validate_acceptance

                validate_acceptance(state)
                incoming = input_model(
                    acceptance_contract=state["acceptance_contract"],
                    baseline_reproduction=state["baseline_reproduction"],
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
                    repair=RepairView.model_validate(repair_payload)
                    if repair_payload
                    else RepairView(),
                    changed_files=[
                        str(item) for item in (state.get("changed_files") or [])
                    ],
                    # 复核/回归需要看到"模型到底改了哪几行"。原先这里只塞了 scope/totals
                    # 这类统计数字（甚至取不到就退化成空串），审查环节无法判断改动合理性。
                    diff_summary=_diff_summary(
                        store,
                        str(state["run_id"]),
                        str(state.get("task_id") or ""),
                        state.get("gate_result") or {},
                    ),
                    gate_result=dict(state.get("gate_result") or {}),
                    verify_result=dict(state.get("verify_result") or {}),
                    regression_result=dict(state.get("regression_result") or {}),
                    check_passed=bool((state.get("gate_result") or {}).get("passed")),
                    baseline_dir=str(state.get("baseline_dir") or ""),
                    syntax_passed=bool(
                        (state.get("syntax_result") or {}).get("passed")
                    ),
                    business_verification_status=str(
                        (state.get("verify_result") or {}).get("status", "unverified")
                    ),
                    verify_passed=bool(
                        (state.get("verify_result") or {}).get("passed")
                    ),
                    regression_passed=bool(
                        (state.get("regression_result") or {}).get("passed")
                    ),
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
            record.output = {
                "failure_reason": public_exception(error, prefix="Agent 输入构造失败")
            }
            record.finished_at = utc_now()
            record.duration_ms = round((time.perf_counter() - started) * 1000, 2)
            store.save(run_id, record, task_id)
            patch = {
                "last_result": "failed",
                "retry_reason": public_exception(
                    error, prefix=f"{spec.name} 输入构造失败"
                ),
                "retry_target": spec.name,
                "terminal_status": "failed",
                "attempt": int(state.get("attempt") or 1),
            }
            from app.workflow.handoff import apply_step_result, outcome_of

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
            # Agent 调用通常持续数分钟：先写入 running 记录，让节点轨和步骤详情实时反映执行中。
            store.save(
                run_id,
                record,
                task_id,
                input_view=spec.input_record,
                output_view=spec.output_record,
            )
            final = graph.invoke(graph_input)
            record.duration_ms = round((time.perf_counter() - started) * 1000, 2)
            from app.infrastructure.records.policy import debug_artifacts

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
            # 只取 final 已有的字段构造 view；缺失字段让 model_validate 报错而不是尝试无参实例化（部分模型有必需字段）。
            view = view_model.model_validate(
                {key: final[key] for key in view_model.model_fields if key in final}
            )

            if kind == "repair":
                is_passed = (
                    bool(final.get("passed"))
                    or str(final.get("result") or "").lower() == "passed"
                )
                view = view.model_copy(
                    update={
                        "passed": is_passed,
                        "result": "passed" if is_passed else "failed",
                    }
                )
            elif kind == "locate":
                is_passed = str(final.get("status") or "") == "LOCATED" and bool(
                    final.get("passed")
                )
                view = view.model_copy(update={"passed": is_passed})
            else:
                raw_verdict = str(final.get("verdict") or "").upper()
                is_passed = bool(final.get("passed")) or raw_verdict == "PASS"
                verdict = (
                    "MANUAL"
                    if raw_verdict == "MANUAL"
                    else ("PASS" if is_passed else "FAIL")
                )
                view = view.model_copy(update={"passed": is_passed, "verdict": verdict})
            # agent 跑完了就是执行成功，结论进 result：判定失败不等于节点执行异常。
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
            conclusion = (
                (
                    "MANUAL"
                    if view.result == "failed"
                    and view.change_plan is not None
                    and view.change_plan.scope == "manual"
                    else "FAIL"
                    if view.result == "failed"
                    else None
                )
                if kind == "repair"
                else str(view.status)
                if kind == "locate"
                else str(view.verdict)
            )
            status, business = map_outcome(kind="ok", business=conclusion)
            record.status = status
            record.result = business
            record.output = dump_model(view)
            record.summary = summary_of(record.output) or (
                str(view.analysis) if kind == "repair" else ""
            )
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
                                "tool_call_count": int(
                                    audit.get("tool_call_count") or 0
                                ),
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
            patch = {
                "last_result": "failed",
                "retry_reason": public_exception(error, prefix=f"{spec.name} 执行异常"),
                "retry_target": spec.name,
                "terminal_status": "failed",
                "attempt": int(state.get("attempt") or 1),
            }
            from app.workflow.handoff import apply_step_result, outcome_of

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
        from app.workflow.handoff import outcome_of

        formal = outcome_of(
            record.output, summary=record.summary, business=record.result
        )
        record.execution, record.assessment = formal.execution, formal.assessment
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
            reason = "\n".join(
                filter(
                    None,
                    [
                        view.summary,
                        f"发现项: {', '.join(view.findings)}" if view.findings else "",
                        f"要求: {', '.join(view.required_changes)}"
                        if view.required_changes
                        else "",
                    ],
                )
            )
        else:
            reason = view.summary
        patch: dict[str, Any] = {
            kind: view.model_dump(),
            "last_result": "failed" if failed else "passed",
            "next_step": None,
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
                )
                or (kind == "review" and str(view.verdict or "").upper() == "MANUAL")
                else "failed"
                if failed
                else "passed"
            ),
        }
        if kind == "locate":
            patch["last_result"] = "manual" if failed else "passed"
            patch["scope_escalation_required"] = bool(
                final.get("scope_escalation_required")
            ) or bool(view.recommended_scope == "project")
        if kind == "review":
            patch["review_contract_error"] = view.contract_error
            # 此节点只保存审查意见，交付和恢复由 decide_validation 决定。
            patch["terminal_status"] = "passed"
        if failed and kind == "repair":
            patch["retry_target"] = "repair_agent"
        elif failed and kind == "review":
            patch["retry_target"] = (
                "review_agent"
                if (final.get("_audit") or {}).get("blocked_by") == "stale_evidence"
                else "repair_agent"
            )
        if kind == "repair":
            repair_files = [str(item) for item in (final.get("changed_files") or [])]
            change_plan = (
                view.change_plan.model_dump(mode="json")
                if view.change_plan is not None
                else {}
            )
            locate = dict(state.get("locate") or {})
            patch["repair_changed_files"] = repair_files
            patch["changed_files"] = repair_files
            patch["change_plan"] = change_plan
            patch["blocked_reason"] = str(final.get("blocked_reason") or "")
            if failed and str(change_plan.get("scope") or "") == "manual":
                # 显式人工方案缺少自动继续条件，重复调用模型不会补齐证据。
                patch.update(
                    last_result="manual", terminal_status="manual", retry_target=""
                )
            # 修复 Agent 的输出不能抹掉定位阶段已经确定的共享层升级信号。
            scope_escalation = bool(
                final.get("scope_escalation_required")
                or state.get("scope_escalation_required")
                or str(locate.get("recommended_scope") or "") == "project"
            )
            patch["scope_escalation_required"] = scope_escalation
            if scope_escalation and not change_plan:
                # 不允许共享层改动在没有可审方案时被重试/收尾悄悄吞掉。
                patch.update(
                    {
                        "last_result": "manual",
                        "terminal_status": "manual",
                    }
                )
            else:
                if scope_escalation:
                    patch["terminal_status"] = "manual"
        if cancellation_requested(store.root, run_id):
            patch.update(
                {
                    "last_result": "cancelled",
                    "terminal_status": "CANCELLED",
                    "next_step": FINALIZE,
                }
            )
        from app.workflow.handoff import apply_step_result, outcome_of

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
