"""工作台工作流后台执行、生命周期租约维护与恢复调度。"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, HTTPException

from app.application.audit import StepError
from app.domain.repair_scope import legacy_analysis_required
from app.infrastructure.records.lifecycle import LifecycleStore
from app.infrastructure.records.store import RecordStore
from app.infrastructure.redaction import public_exception
from app.workflow.policy import AgentPolicy
from config.settings import settings
from workbench.context import (
    _config,
    _parse_time,
    _read_json,
    _run_lock,
    _RUN_LOCKS_GUARD,
    _safe_id,
    _STARTED_RUNS,
    _store,
)
from workbench.control_store import _audit_control, _control_db

logger = logging.getLogger("workbench.lifecycle")

def _start_lifecycle_heartbeat(run_id: str) -> tuple[threading.Event, threading.Thread]:
    """Keep an explicit lease while a worker owns the run lock, including long Agent calls."""
    store = _store()
    lifecycle = LifecycleStore(store.root)
    stop = threading.Event()

    def pulse() -> None:
        while not stop.wait(5):
            try:
                lifecycle.update_run(run_id, heartbeat_at=datetime.now(timezone.utc).isoformat(), worker_active=True)
            except Exception:
                logger.exception("更新运行心跳失败 run_id=%s", run_id)

    thread = threading.Thread(target=pulse, name=f"run-heartbeat-{run_id}", daemon=True)
    thread.start()
    return stop, thread

def _clear_worker_active(run_id: str) -> None:
    """清理 Run 的活动租约。

    与工作区回收解耦：生命周期写入失败重试一次，仍然失败只记录日志，
    后续工作区回收照常执行，不能因为一个字段写不进去就跳过清理。
    """
    for attempt in range(2):
        try:
            LifecycleStore(_store().root).update_run(run_id, worker_active=False)
            return
        except Exception:
            logger.exception(
                "清理 Run 活动租约失败 run_id=%s attempt=%s",
                run_id, attempt + 1,
            )

def _complete_run_lifecycle(run_id: str, store: RecordStore) -> None:
    """Settle the run only after graph.invoke has returned; waiting interrupts are not failures."""
    lifecycle = LifecycleStore(store.root)
    report = _read_json(store.run_dir(run_id) / "reports" / "final.json", {})
    if isinstance(report, dict) and report:
        result = str(report.get("result") or report.get("status") or "").upper()
        if result in {"SUCCESS", "SUCCEEDED", "COMPLETED", "DELIVERED"}:
            status = "succeeded"
        elif result in {"CANCELLED", "CANCELED"}:
            status = "cancelled"
        elif result == "PAUSED":
            # 暂停/单步等待不是失败；保持可恢复状态，等待控制面继续。
            status = "paused"
        elif result in {"MANUAL", "MANUAL_REVIEW_REQUIRED", "INSUFFICIENT_EVIDENCE", "BLOCKED"}:
            status = "partial_failed"
        elif result in {"PARTIAL_FAILED", "PARTIAL_FAILURE"}:
            status = "partial_failed"
        else:
            status = "failed" if result in {"FAILED", "FAIL", "ERROR", "REJECTED"} else "succeeded"
        lifecycle.update_run(
            run_id, status=status,
            phase=str(report.get("terminal_step") or "workflow"),
            current_step=str(report.get("terminal_step") or ""),
            terminal_reason=str(report.get("terminal_reason") or ""),
            next_action=str(report.get("next_action") or ""),
            error_type="WORKFLOW_FAILED" if status == "failed" else "",
            error_message=str(report.get("terminal_reason") or "") if status == "failed" else "",
        )
        return

    run_steps = store.list_steps(run_id)
    if any(step.status == "waiting" for step in run_steps):
        lifecycle.update_run(run_id, status="paused", phase="paused", current_step="")
        return
    payload = _read_json(store.steps_path(run_id), {})
    slots = payload.get("tasks", []) if isinstance(payload, dict) else []
    task_states = []
    if isinstance(slots, list):
        for slot in slots:
            if isinstance(slot, dict) and isinstance(slot.get("task_id"), str):
                task_states.append(lifecycle.read_task(run_id, slot["task_id"]))
    if any(item.get("status") == "waiting_approval" for item in task_states):
        lifecycle.update_run(run_id, status="waiting_approval", phase="request_approval", current_step="request_approval")
        return
    if any(item.get("status") == "paused" for item in task_states):
        lifecycle.update_run(run_id, status="paused", phase="paused", current_step="")
        return

    reason = "工作流已退出，但未生成终态报告或可恢复的等待状态。"
    lifecycle.update_run(
        run_id, status="failed", phase="workflow_exit", current_step="",
        error_type="MISSING_TERMINAL_REPORT", error_message=reason,
        terminal_reason=reason, next_action="检查 Run 与 Task 生命周期记录后，从 Run 控制栏重试。",
    )
    _settle_task_lifecycles_after_run_failure(
        run_id, store, phase="workflow_exit", reason=reason,
        next_action="检查失败节点后使用 Run 级重试恢复。",
    )

def _settle_task_lifecycles_after_run_failure(
    run_id: str,
    store: RecordStore,
    *,
    phase: str,
    reason: str,
    next_action: str,
) -> None:
    from app.application.audit import StepError

    lifecycle = LifecycleStore(store.root)
    payload = _read_json(store.steps_path(run_id), {})
    slots = payload.get("tasks", []) if isinstance(payload, dict) else []
    if not isinstance(slots, list):
        return
    for slot in slots:
        if not isinstance(slot, dict) or not isinstance(slot.get("task_id"), str):
            continue
        task_id = slot["task_id"]
        state = lifecycle.read_task(run_id, task_id)
        status = str(state.get("status") or "")
        if phase in {"approval_recovery", "worker_timeout"} and status == "waiting_approval":
            continue
        if status in {"succeeded", "partial_failed", "failed", "cancelled", "manual", "skipped", "blocked"}:
            continue
        records = store.list_steps(run_id, task_id)
        finalizer = next((record for record in records if record.name == "finalize_execution"), None)
        if finalizer and finalizer.status == "succeeded" and not any(record.status == "running" for record in records):
            store.complete_task(run_id, task_id)
            continue
        for record in records:
            if record.status == "running":
                finished = datetime.now(timezone.utc)
                started = _parse_time(record.started_at)
                record.status = "failed"
                record.finished_at = finished.isoformat()
                record.duration_ms = max(0, (finished - started).total_seconds() * 1000) if started else 0
                record.error = StepError(code="STEP_EXECUTION_FAILED", message=reason)
                store.save(run_id, record, task_id)
        lifecycle.update_task(
            run_id, task_id, status="failed",
            terminal_step=str(state.get("current_step") or phase),
            terminal_reason=reason, next_action=next_action,
            error={"code": "RUN_WORKER_FAILED", "message": reason},
        )

def _discard_retry_archive(store: RecordStore, run_id: str) -> None:
    """恢复已产生新报告后清掉启动时归档的上一轮报告。"""
    reports = store.run_dir(run_id) / "reports"
    for name in ("run_failure.json", "final.json"):
        try:
            (reports / f"{name}.retry_previous").unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "清理归档报告失败 run_id=%s name=%s", run_id, name, exc_info=True
            )

def _record_background_failure(
    run_id: str,
    error: Exception,
    *,
    phase: str = "workflow_startup",
) -> None:
    """将图外异常写成可查询的终态产物，避免后台任务失败后 Run 永久显示运行中。"""
    try:
        logger.error("后台工作流异常 run_id=%s", run_id, exc_info=(type(error), error, error.__traceback__))
        # 取消优先于异常：用户停止后节点抛出的异常不能把本轮改写成 FAILED，
        # 也不能写出 run_failure.json 误导后续恢复判定。
        from app.infrastructure.run_control import cancellation_requested

        try:
            cancelled = cancellation_requested(_store().root, run_id)
        except Exception:
            cancelled = False
        if cancelled:
            logger.info(
                "后台异常发生在停止请求之后，按 CANCELLED 收敛 run_id=%s error=%s",
                run_id, type(error).__name__,
            )
            from app.entrypoint import _write_run_report

            _write_run_report(_store(), run_id)
            LifecycleStore(_store().root).update_run(run_id, status="cancelled", worker_active=False)
            return
        directory = _store().run_dir(_safe_id(run_id))
        if not directory.is_dir():
            return
        report_dir = directory / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        target = report_dir / "run_failure.json"
        temporary = report_dir / "run_failure.json.tmp"
        now = datetime.now(timezone.utc).isoformat()
        # Persist only the redacted public exception summary: business failures retain
        # actionable detail; unexpected exceptions reveal only their type, never raw inputs.
        reason = public_exception(error, prefix="后台工作流异常")
        next_action = (
            "检查审批恢复时的服务端异常；确认环境正常后，可回到交付审批重新提交决策。"
            if phase == "approval_recovery"
            else "检查工作流恢复时的服务端异常；修复原因后再恢复本轮执行。"
            if phase == "workflow_resume"
            else "检查启动配置、工作流装配和服务端异常；修复原因后重新执行。"
        )
        terminal_step = phase
        if phase in {"workflow_startup", "workflow_resume"}:
            failed = [record for record in _store().list_steps(run_id) if record.status == "failed"]
            if failed:
                record = failed[-1]
                terminal_step = record.name
                reason = str(record.output.get("failure_reason") or record.summary or reason)
                attempts = int(record.output.get("automatic_attempt") or record.attempt or 1)
                retry_count = int(record.output.get("retry_count", max(0, attempts - 1)))
                exhausted = bool(record.output.get("retry_exhausted"))
                reason += f"；已执行 {attempts} 次（自动重试 {retry_count} 次）"
                if exhausted:
                    reason += "，重试次数已耗尽"
                if record.name == "sync_source_repo":
                    next_action = "检查远程 Git 仓库的网络连通性、TLS/证书、代理和访问凭据；排除原因后从源仓库同步节点恢复。"
        payload = {
            "status": "FAILED", "error_type": type(error).__name__,
            "message": reason,
            "terminal_step": terminal_step, "terminal_reason": reason,
            "next_action": next_action,
            "failed_at": now,
        }
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        try:
            lifecycle = LifecycleStore(_store().root)
            lifecycle.update_run(
                run_id, status="failed", phase=terminal_step, current_step=terminal_step,
                error_type=type(error).__name__, error_message=reason,
                terminal_reason=reason, next_action=next_action,
            )
            _settle_task_lifecycles_after_run_failure(
                run_id, _store(), phase=phase, reason=reason, next_action=next_action,
            )
        except Exception:
            logger.exception("后台失败报告已保存，但生命周期收敛失败 run_id=%s", run_id)
        final_path = report_dir / "final.json"
        existing_report = _read_json(final_path, {})
        if not existing_report:
            # 恢复模式启动时把上一轮报告归档为 *.retry_previous；本次恢复失败
            # 时用它兜底，避免新报告把任务明细退化成空列表。
            existing_report = _read_json(report_dir / "final.json.retry_previous", {})
        if not isinstance(existing_report, dict):
            existing_report = {}
        final_tmp = report_dir / "final.json.tmp"
        final_report = {
            **existing_report,
            "report_version": max(int(existing_report.get("report_version") or 0), 1),
            "report_kind": "run_final", "run_id": run_id,
            "result": "FAILED", "status": "FAILED", "terminal_step": payload["terminal_step"],
            "terminal_reason": payload["terminal_reason"], "next_action": payload["next_action"],
            "finished_at": now,
            "task_count": int(existing_report.get("task_count") or 0),
            "task_results": existing_report.get("task_results") if isinstance(existing_report.get("task_results"), list) else [],
        }
        final_tmp.write_text(json.dumps(final_report, ensure_ascii=False, indent=2), encoding="utf-8")
        final_tmp.replace(final_path)
    except Exception:
        logger.exception("无法写入后台失败报告 run_id=%s", run_id)

def _run_workflow_background_v1(
    run_id: str, overrides: dict[str, Any], run_lock: Any = None
) -> None:
    from app.entrypoint import run_workflow
    if run_lock is None:
        with _RUN_LOCKS_GUARD:
            run_lock = _run_lock(run_id)
        if not run_lock.acquire(blocking=False):
            logger.warning("跳过重复启动的工作流 run_id=%s", run_id)
            return
    heartbeat_stop: threading.Event | None = None
    heartbeat_thread: threading.Thread | None = None
    try:
        store = _store()
        LifecycleStore(store.root).update_run(
            run_id, status="running", phase="workflow_startup", current_step="", error={}, worker_active=True,
            allow_reactivate=True,
        )
        heartbeat_stop, heartbeat_thread = _start_lifecycle_heartbeat(run_id)
        run_workflow(run_id, overrides=overrides, prepared=True)
        _complete_run_lifecycle(run_id, store)
    except Exception as exc:
        logger.exception("工作流后台执行失败 run_id=%s", run_id)
        _record_background_failure(run_id, exc)
    finally:
        if heartbeat_stop is not None:
            heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=1)
        from app.infrastructure.runtime import close_sessions
        close_sessions(run_id)
        _clear_worker_active(run_id)
        try:
            from app.infrastructure.filesystem.workspace_gc import (
                collect_configured_workspaces,
            )
            collect_configured_workspaces(_config())
        except Exception:
            logger.exception("回收配置工作区失败 run_id=%s", run_id)
        run_lock.release()
        with _RUN_LOCKS_GUARD:
            _STARTED_RUNS.discard(run_id)

def _resume_workflow_background(
    run_id: str,
    run_lock: Any = None,
    resume_payload: dict[str, Any] | None = None,
) -> None:
    if run_lock is None:
        with _RUN_LOCKS_GUARD:
            run_lock = _run_lock(run_id)
        if not run_lock.acquire(blocking=False):
            logger.warning("跳过重复恢复的工作流 run_id=%s", run_id)
            return
    heartbeat_stop: threading.Event | None = None
    heartbeat_thread: threading.Thread | None = None
    try:
        from app.infrastructure.config import load_config, workflow_recursion_limit
        from app.workflow.wiring import build_graph, build_services

        store = _store()
        LifecycleStore(store.root).update_run(
            run_id, status="running", phase="workflow_resume", current_step="", error={}, worker_active=True,
            terminal_reason="", error_type="", error_message="", next_action="",
            allow_reactivate=True,
        )
        heartbeat_stop, heartbeat_thread = _start_lifecycle_heartbeat(run_id)
        runtime_config = load_config()
        # 恢复时让 checkpoint 指向的 sync_source_repo 节点执行；
        # 不能在 graph.invoke 之前再次同步，否则原生重试仍然无法接管。
        source_repo = runtime_config.source_repo
        graph = build_graph(
            store=store,
            services=build_services(
                config=runtime_config,
                source_repo=source_repo,
            ),
            config=runtime_config,
        )
        loader = getattr(store, "load_run", None)
        run_meta = loader(run_id) if callable(loader) else _read_json(store.run_dir(run_id) / "run.json", {})
        if not isinstance(run_meta, dict):
            run_meta = {}
        config = {
            "configurable": {"thread_id": run_id},
            "recursion_limit": workflow_recursion_limit(
                max_attempts=int(run_meta.get("max_attempts") or settings.default_max_attempts),
                task_count_hint=int(run_meta.get("log_limit") or 50),
            ),
        }
        snapshot = graph.get_state(config)
        _renew_open_claim(store, run_id, snapshot)
        has_interrupt = any(getattr(task, "interrupts", ()) for task in (getattr(snapshot, "tasks", ()) or ()))
        if resume_payload is not None:
            from langgraph.types import Command

            from app.workflow.wiring import resume_task_approval

            task_id = str(resume_payload.get("task_id") or "")
            detached = bool(task_id) and resume_task_approval(
                store, run_id, task_id, resume_payload, runtime_config,
                build_services(config=runtime_config, source_repo=source_repo),
            )
            # 嵌套任务子图的 interrupt 在部分 LangGraph 版本中不一定完整暴露在父快照的
            # task.interrupts 上。审批接口已校验当前任务处于待决状态且持有 run 锁，因此
            # 必须直接把决定交给 checkpoint；绝不能因快照未显示 interrupt 而重新 invoke(None)。
            if detached:
                from app.workflow.validation import assert_workflow_version
                assert_workflow_version(_store().load_run(run_id))
                final_state = graph.invoke(None, config=config) if snapshot.next else dict(snapshot.values)
            else:
                from app.workflow.validation import assert_workflow_version
                assert_workflow_version(_store().load_run(run_id))
                final_state = graph.invoke(Command(resume=resume_payload), config=config)
        elif has_interrupt:
            from langgraph.types import Command

            from app.workflow.validation import assert_workflow_version
            assert_workflow_version(_store().load_run(run_id))
            final_state = graph.invoke(Command(resume={"control": "resume"}), config=config)
        else:
            from app.workflow.validation import assert_workflow_version
            assert_workflow_version(_store().load_run(run_id))
            final_state = graph.invoke(None, config=config)
        # 本次恢复已成功推进 checkpoint；清理之前后台异常写入的失败标记，避免 UI 继续把已恢复运行判为 FAILED。
        failure_path = store.run_dir(run_id) / "reports" / "run_failure.json"
        if failure_path.is_file():
            failure_path.unlink()
        stale_run_report = store.run_dir(run_id) / "reports" / "final.json"
        stale_payload = _read_json(stale_run_report, {})
        # 上一次终态的 FAILED 报告（含业务阶段失败）在本次恢复推进 checkpoint 后
        # 全部作废：终态结束会被新报告覆盖，等待审批则不能让旧失败顶替当前状态。
        if (
            isinstance(stale_payload, dict)
            and str(stale_payload.get("result") or stale_payload.get("status") or "").upper()
            in {"FAILED", "FAIL"}
        ):
            stale_run_report.unlink(missing_ok=True)
        from app.entrypoint import _write_run_report_if_terminal
        _write_run_report_if_terminal(store, run_id, graph, final_state=final_state)
        _complete_run_lifecycle(run_id, store)
        if (store.run_dir(run_id) / "reports" / "final.json").is_file():
            _discard_retry_archive(store, run_id)
    except Exception as exc:
        logger.exception("恢复工作流失败 run_id=%s", run_id)
        phase = "approval_recovery" if resume_payload is not None else "workflow_resume"
        _record_background_failure(run_id, exc, phase=phase)
    finally:
        from app.infrastructure.runtime import close_sessions
        close_sessions(run_id)
        if heartbeat_stop is not None:
            heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=1)
        _clear_worker_active(run_id)
        try:
            from app.infrastructure.filesystem.workspace_gc import (
                collect_configured_workspaces,
            )
            collect_configured_workspaces(_config())
        except Exception:
            logger.exception("回收恢复 Run 的工作区失败 run_id=%s", run_id)
        run_lock.release()

def _renew_open_claim(store: RecordStore, run_id: str, snapshot: Any) -> None:
    """恢复未终态任务前续租本运行的 claim；没有可续租记录时不补领。"""
    task_id = _claimed_task_id(snapshot)
    if not task_id:
        return
    from app.workflow.steps.claim_repair_slot.node import renew_claim

    try:
        renewed = renew_claim(store.root, run_id=run_id, task_id=task_id)
    except Exception as error:
        logger.error("恢复前续租修复资格失败 run_id=%s task_id=%s", run_id, task_id, exc_info=True)
        # 续租失败后继续恢复会让 claim 过期，另一路可能同时修复同一问题。
        raise RuntimeError("无法续租修复资格，终止恢复以避免并发修复") from error
    if not renewed:
        logger.error("恢复时没有可续租的修复资格 run_id=%s task_id=%s", run_id, task_id)
        raise RuntimeError("修复资格已过期或不存在，终止恢复以避免并发修复")

def _claimed_task_id(snapshot: Any) -> str:
    """从父图或当前任务子图状态里取出已领取资格的任务。"""
    states = [getattr(snapshot, "values", None)]
    states.extend(getattr(task, "state", None) for task in (getattr(snapshot, "tasks", ()) or ()))
    for values in states:
        payload = getattr(values, "values", values)
        if isinstance(payload, dict) and payload.get("repair_slot_claimed"):
            task_id = str(payload.get("task_id") or "")
            if task_id:
                return task_id
    return ""

def _reinvestigate_task(run_id: str, task_id: str, background: BackgroundTasks) -> dict[str, Any]:
    """为人工任务建立独立调查 Run，保留原报告、分支、回执和审批检查点。"""
    import shutil

    from app.application.audit import StepRecord
    from app.infrastructure.config import load_config
    from app.workflow.routes import IDENTIFY
    from app.workflow.wiring import build_graph, build_services

    store = _store()
    config = load_config()
    graph = build_graph(store=store, services=build_services(config=config, source_repo=config.source_repo), config=config)
    snapshot = graph.get_state({"configurable": {"thread_id": run_id}})
    values = dict(snapshot.values or {})
    original = next((item for item in values.get("tasks", []) if item.get("task_id") == task_id), None)
    if original is None:
        raise HTTPException(status_code=409, detail="检查点中缺少原始任务输入，无法重新调查")
    new_id = uuid.uuid4().hex
    task = {**original, "problem_key": str(original.get("problem_key") or task_id) + "-revision-" + new_id[:8]}
    task.pop("repair_branch", None)
    task["_workflow_attempt"] = 1
    metadata = {**store.load_run(run_id), "parent_run_id": run_id, "parent_task_id": task_id,
                "reinvestigation": True, "startup_state": "started"}
    store.create_run(new_id, metadata)
    artifact_dir = store.run_dir(run_id) / "artifacts"
    if artifact_dir.is_dir():
        shutil.copytree(artifact_dir, store.run_dir(new_id) / "artifacts")
    for record in store.list_steps(run_id):
        if record.name not in {"sync_source_repo", "discover_logs", "normalize_error", "deduplicate_error", "identify_carrier"}:
            continue
        payload = record.model_dump()
        payload["input"] = {**payload.get("input", {}), "run_id": new_id} if "run_id" in payload.get("input", {}) else payload.get("input", {})
        store.save(new_id, StepRecord.model_validate(payload))
    values.update(run_id=new_id, tasks=[task], task_cursor=0, task_results=[], task_id="", task={},
                  attempt=1, automatic_attempt=1, terminal_status="", task_summary={}, last_result="passed")
    graph.update_state({"configurable": {"thread_id": new_id}}, values, as_node=IDENTIFY)
    LifecycleStore(store.root).update_run(new_id, status="queued", worker_active=False, phase="reinvestigation")
    _audit_control(new_id, task_id, "task.reinvestigate_requested", "api", f"parent_run={run_id}")
    background.add_task(_resume_workflow_background, new_id)
    return {"run_id": new_id, "task_id": task_id, "status": "retry_requested",
            "message": "已创建独立调查执行；原人工报告、分支和审批记录保留。"}
