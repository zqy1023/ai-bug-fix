"""任务详情与单步调试路由。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from server.routes.common import *

router = APIRouter()

@router.get("/api/v1/runs/{run_id}/tasks/{task_id}")
def v1_get_task(run_id: str, task_id: str, _: None = Depends(_authorize)) -> dict[str, Any]:
    """只返回一个任务的详情，避免 Run 页面携带并渲染所有任务的步骤与产物。"""
    store = _store()
    run_id, task_id = _safe_id(run_id), _safe_id(task_id)
    payload = _run_payload(store, run_id)
    source = next((item for item in payload.get("tasks", []) if item.get("task_id") == task_id), None)
    if source is None:
        raise HTTPException(status_code=404, detail="该执行中不存在此任务")
    steps = [_formal_step_payload(store, run_id, task_id, step) for step in source.get("steps") or []]
    report = source.get("report") if isinstance(source.get("report"), dict) else {}
    artifacts = []
    for step in steps:
        for ref in step.get("artifacts") or []:
            artifacts.append({
                "name": ref.get("name") or "artifact",
                "path": ref.get("path") or "",
                "media_type": ref.get("media_type") or "application/json",
                "description": str(step.get("name") or ""),
                "size_bytes": None,
                "task_id": task_id,
            })
    if (store.task_dir(run_id, task_id) / "reports" / "final.json").is_file():
        artifacts.append({
            "name": "任务终态报告", "path": "reports/final.json",
            "media_type": "application/json", "description": "最终结果与处置原因",
            "size_bytes": None, "task_id": task_id,
        })
    run_meta = _read_json(store.run_dir(run_id) / "run.json", {})
    run_error = payload.get("run_error") or {}
    from core.contracts import WORKFLOW_VERSION
    current = run_meta.get("workflow_version") == WORKFLOW_VERSION
    retry = task_retry_action(
        current=current, worker_active=bool(payload.get("worker_active")),
        run_failed=(store.run_dir(run_id) / "reports/run_failure.json").is_file(),
        run_status=_v1_run_summary(payload)["status"],
        task={**source, "result": source.get("status"), "steps": steps},
    )
    return {
        "read_only": not current,
        "actions": {"retry": retry.model_dump(mode="json")},
        "delivery": delivery_view(steps).model_dump(mode="json"),
        "run_id": run_id,
        "run_status": _v1_run_summary(payload)["status"],
        "worker_active": bool(payload.get("worker_active")),
        "run_terminal": bool(payload.get("terminal")),
        "run_error": run_error if isinstance(run_error, dict) else {},
        "stages": task_stages(),
        "task": {
            "task_id": task_id, "carrier": source.get("carrier", ""),
            "biztype": source.get("biztype", ""), "result": source.get("status", "PENDING"),
            "problem": source.get("problem") or {}, "approval_pending": bool(source.get("approval_pending")),
            "lifecycle": source.get("lifecycle") if isinstance(source.get("lifecycle"), dict) else {},
            "current_step": str((source.get("lifecycle") or {}).get("current_step") or ""),
            "attempt": int((source.get("lifecycle") or {}).get("attempt") or 0),
            "steps": steps, "report": report,
        },
        "artifacts": artifacts,
        "config": {
            **_config().run_parameters(run_meta),
        },
    }


@router.post("/api/v1/runs/{run_id}/tasks/{task_id}/retry", status_code=202)
def v1_retry_task(
    run_id: str, task_id: str, background: BackgroundTasks,
    _: None = Depends(_authorize),
) -> dict[str, Any]:
    """Retry one terminal failed task in the same run without replaying already completed tasks."""
    _require_current_workflow(run_id)
    from infrastructure.llm.config import load_config, workflow_recursion_limit
    from infrastructure.workspace.workspace import WorkspaceManager
    from workflow.engine.graph import TASK_DISPATCH
    from workflow.engine.wiring import build_graph, build_services

    run_id, task_id = _safe_id(run_id), _safe_id(task_id)
    store = _store()
    detail = _v1_detail(run_id)
    task_detail = v1_get_task(run_id, task_id, None)
    task_payload = task_detail["task"]
    action = task_detail["actions"]["retry"]
    if not action["enabled"]:
        raise HTTPException(status_code=409, detail=action["reason"])
    task_result = str(task_payload.get("result") or "").upper()
    if task_result in {"MANUAL", "INSUFFICIENT_EVIDENCE", "BLOCKED"}:
        return _reinvestigate_task(run_id, task_id, background)
    task_steps = list(task_payload.get("steps") or [])

    with _RUN_LOCKS_GUARD:
        lock = _run_lock(run_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该 Run 的后台线程仍持有执行锁，暂不能重试")

    baseline_archive: Path | None = None
    baseline_original: Path | None = None
    workspace_archive: Path | None = None
    workspace_original: Path | None = None
    checkpoint_updated = False
    try:
        runtime_config = load_config()
        graph = build_graph(
            store=store,
            services=build_services(config=runtime_config, source_repo=runtime_config.source_repo),
            config=runtime_config,
        )
        cfg = {
            "configurable": {"thread_id": run_id},
            "recursion_limit": workflow_recursion_limit(
                max_attempts=int(detail.get("config", {}).get("max_attempts") or settings.default_max_attempts),
                task_count_hint=int(detail.get("config", {}).get("log_limit") or 50),
            ),
        }
        snapshot = graph.get_state(cfg)
        values = getattr(snapshot, "values", None)
        next_nodes = [str(node) for node in (getattr(snapshot, "next", ()) or ())]
        if not isinstance(values, dict) or next_nodes:
            raise HTTPException(status_code=409, detail="当前 LangGraph checkpoint 尚未处于可安全重试的终态")
        if any(getattr(task, "interrupts", ()) for task in (getattr(snapshot, "tasks", ()) or ())):
            raise HTTPException(status_code=409, detail="当前 Run 存在中断等待，必须先完成暂停或审批决策")

        original_tasks = list(values.get("tasks") or [])
        task_index = next(
            (index for index, item in enumerate(original_tasks)
             if isinstance(item, dict) and str(item.get("task_id") or "") == task_id),
            None,
        )
        if task_index is None:
            raise HTTPException(status_code=409, detail="LangGraph checkpoint 中找不到该 Task 的原始输入")
        original_task = dict(original_tasks[task_index])
        next_attempt = max(
            int((task_payload.get("lifecycle") or {}).get("attempt") or 0),
            max((int(step.get("attempt") or 0) for step in task_steps), default=0),
            0,
        ) + 1

        from workflow.engine.step_runner import state_from_records
        from workflow.engine.retry_context import capture_retry_context
        previous_state = state_from_records(store, run_id, task_id)
        previous_state["retry_reason"] = str((task_payload.get("lifecycle") or {}).get("terminal_reason") or "人工请求重试失败任务")
        previous_state["attempt"] = next_attempt - 1
        original_task["_retry_context"] = capture_retry_context(previous_state, store)

        # A fresh source snapshot must be generated for a whole-task retry; retain the previous
        # immutable baseline for audit and free the canonical path expected by prepare_source_snapshot.
        snapshot_record = next(
            (step for step in reversed(task_steps) if step.get("name") == "prepare_source_snapshot"),
            None,
        )
        snapshot_output = snapshot_record.get("output") if isinstance(snapshot_record, dict) and isinstance(snapshot_record.get("output"), dict) else {}
        baseline_value = str(snapshot_output.get("baseline_dir") or "")
        if baseline_value:
            baseline_original = Path(baseline_value).resolve()
            task_root = store.task_dir(run_id, task_id).resolve()
            if not baseline_original.is_relative_to(task_root):
                raise HTTPException(status_code=409, detail="已保存的源码基线不在当前 Task 目录内，拒绝移动")
            if baseline_original.exists():
                baseline_archive = baseline_original.with_name(
                    f"{baseline_original.name}.previous-attempt-{next_attempt}-{uuid.uuid4().hex[:8]}"
                )
                baseline_original.replace(baseline_archive)

        # prepare_workspace refuses to overwrite a task directory. Archive the old writable
        # workspace as well, so the retry gets a clean destination and the failed attempt remains inspectable.
        workspace_root = runtime_config.workspace_root.resolve()
        workspace_original = (
            workspace_root / "ai" / WorkspaceManager._safe_name(f"{run_id}-{task_id}")
        ).resolve()
        if not workspace_original.is_relative_to(workspace_root):
            raise HTTPException(status_code=409, detail="Task 隔离工作区路径越界，拒绝重试")
        if workspace_original.exists():
            workspace_archive = workspace_original.with_name(
                f"{workspace_original.name}.previous-attempt-{next_attempt}-{uuid.uuid4().hex[:8]}"
            )
            workspace_original.replace(workspace_archive)

        appended_task = {**original_task, "_workflow_attempt": next_attempt}
        next_tasks = [*original_tasks, appended_task]
        patch = {
            "tasks": next_tasks,
            "task_cursor": len(original_tasks),
            "task_id": task_id,
            "task": original_task,
            "problem": dict(original_task.get("problem") or {}),
            "terminal_status": "",
            "task_summary": {},
            "attempt": next_attempt,
            "automatic_attempt": 1,
            "retry_reason": "",
            "retry_target": "",
            "last_result": "passed",
        }
        LifecycleStore(store.root).update_run(
            run_id, status="queued", phase="task_retry_queued", current_step="",
            started_at=None, finished_at=None, error={},
            error_type="", error_message="", terminal_reason="", next_action="",
            worker_active=True,
            allow_reactivate=True,
        )
        LifecycleStore(store.root).update_task(
            run_id, task_id, status="retrying", current_step="retry_prepare",
            attempt=next_attempt, terminal_step="", terminal_reason="", next_action="",
            error={}, last_error={}, last_error_summary="",
            allow_reactivate=True,
        )
        connection = _control_db()
        try:
            connection.execute(
                "INSERT INTO run_control(run_id,pause_requested,cancel_requested,updated_at) VALUES(?,0,0,?) "
                "ON CONFLICT(run_id) DO UPDATE SET pause_requested=0,cancel_requested=0,updated_at=excluded.updated_at",
                (run_id, datetime.now(timezone.utc).isoformat()),
            )
            connection.commit()
        finally:
            connection.close()

        # Checkpoint mutation is the commit point for the retry request. No fallible
        # status/audit I/O follows it; artifacts are cleaned best-effort below.
        graph.update_state(cfg, patch, as_node=TASK_DISPATCH)
        checkpoint_updated = True
        for report_path in (
            store.task_dir(run_id, task_id) / "reports" / "final.json",
            store.run_dir(run_id) / "reports" / "final.json",
        ):
            try:
                report_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("旧终态报告清理失败 run_id=%s task_id=%s path=%s", run_id, task_id, report_path, exc_info=True)
        try:
            _audit_control(
                run_id, task_id, "task.retry_requested", "api",
                f"attempt={next_attempt}; baseline_archived={bool(baseline_archive)}; workspace_archived={bool(workspace_archive)}",
            )
        except Exception:
            logger.exception("Task 重试已提交，但审计写入失败 run_id=%s task_id=%s", run_id, task_id)
    except Exception:
        compensation_failures: list[tuple[str, OSError]] = []
        if not checkpoint_updated:
            if baseline_archive is not None and baseline_archive.exists() and baseline_original is not None:
                try:
                    baseline_archive.replace(baseline_original)
                except OSError as error:
                    logger.exception(
                        "Task 重试失败后恢复旧基线失败 run_id=%s task_id=%s path=%s",
                        run_id, task_id, baseline_archive,
                    )
                    compensation_failures.append((str(baseline_archive), error))
            if workspace_archive is not None and workspace_archive.exists() and workspace_original is not None:
                try:
                    workspace_archive.replace(workspace_original)
                except OSError as error:
                    logger.exception(
                        "Task 重试失败后恢复旧工作区失败 run_id=%s task_id=%s path=%s",
                        run_id, task_id, workspace_archive,
                    )
                    compensation_failures.append((str(workspace_archive), error))
            try:
                LifecycleStore(store.root).update_run(
                    run_id, status=str(detail.get("status") or "failed"),
                    worker_active=False,
                )
                LifecycleStore(store.root).update_task(
                    run_id, task_id, status="failed",
                    terminal_step=str((task_payload.get("lifecycle") or {}).get("terminal_step") or "finalize_execution"),
                    terminal_reason=str((task_payload.get("lifecycle") or {}).get("terminal_reason") or "Task 重试未能提交。"),
                    next_action="检查运行记录和源码工作区后重试。",
                    allow_reactivate=True,
                )
            except Exception:
                logger.exception("Task 重试失败后恢复生命周期状态失败 run_id=%s task_id=%s", run_id, task_id)
        lock.release()
        if compensation_failures:
            # 归档回滚失败会让下一次重试读到空路径；必须显式终止并把完整
            # 归档路径返回给调用方，而不是吞掉异常后留下不一致状态。
            paths = "；".join(
                f"{path}（{type(error).__name__}）"
                for path, error in compensation_failures
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "RETRY_COMPENSATION_FAILED",
                    "message": f"Task 重试未提交且原归档未能恢复，请检查归档路径：{paths}",
                },
            ) from compensation_failures[0][1]
        raise

    background.add_task(_resume_workflow_background, run_id, lock)
    return {
        "run_id": run_id,
        "task_id": task_id,
        "status": "retry_requested",
        "attempt": next_attempt,
        "message": "已提交 Task 重试；本轮会追加一次任务执行，不会重复运行此前已完成的后续任务。",
    }


@router.get("/api/v1/runs/{run_id}/tasks/{task_id}/steps/{step_name}")
def v1_get_step(
    run_id: str, task_id: str, step_name: str, _: None = Depends(_authorize),
) -> dict[str, Any]:
    """单步详情：步骤记录、产物引用、关联模型调用与脱敏上下文。"""
    run_id, task_id, step_name = _safe_id(run_id), _safe_id(task_id), _safe_id(step_name)
    store = _store()
    _run_payload(store, run_id)
    record = store.load(run_id, step_name, task_id)
    step_scope = task_id if record is not None else ""
    record = record or store.load(run_id, step_name)
    if record is None:
        raise HTTPException(status_code=404, detail="步骤记录不存在")
    # Agent 记录的 name 是 repair_agent，审计里的 agent_role 是 repair；两者都接受。
    roles = {record.name, record.name.removesuffix("_agent")}
    calls = [
        item for item in _load_model_calls(run_id, task_id)
        if str(item.get("agent_role") or "") in roles
        and int(item.get("attempt") or 0) == int(record.attempt or 0)
    ]
    started = _parse_time(record.started_at)
    finished = _parse_time(record.finished_at)
    if started:
        calls = [
            item for item in calls
            if (moment := _parse_time(item.get("started_at")))
            and moment >= started - timedelta(seconds=5)
            and (finished is None or moment <= finished + timedelta(seconds=5))
        ]
    step_payload = _formal_step_payload(store, run_id, step_scope or "", record.model_dump(mode="json"))
    from workflow.engine.step_runner import StepRunner, StepRunnerError, StepRunRequest
    from server.view_models import ActionAvailability
    action = ActionAvailability(enabled=False)
    try:
        _require_current_workflow(run_id)
        run_detail = _v1_detail(run_id)
        if run_detail.get("worker_active"):
            action = ActionAvailability(enabled=False, reason="后台仍在执行，请等待停止后重试")
        else:
            spec = StepRunner(store, run_status=lambda _: run_detail["status"]).validate(
                StepRunRequest(run_id=run_id, task_id=task_id, step_name=step_name, confirm_write=True)
            )
            action = ActionAvailability(enabled=True, requires_confirmation=spec.requires_confirmation)
    except StepRunnerError as exc:
        action = ActionAvailability(enabled=False, reason=str(exc))
    except HTTPException as exc:
        action = ActionAvailability(enabled=False, reason=str(exc.detail))
    continuation = action.model_copy()
    if action.enabled and (store.run_dir(run_id) / "reports/run_failure.json").is_file():
        continuation = ActionAvailability(enabled=False, reason="批次存在运行级异常，请先重试批次；当前仅允许执行单个步骤")
    return {
        "run_id": run_id,
        "task_id": task_id,
        "step": step_payload,
        "actions": {"rerun": action.model_dump(mode="json"), "continue_after": continuation.model_dump(mode="json")},
        "artifacts": [ref.model_dump(mode="json") for ref in record.artifacts],
        "model_call_ids": [str(item.get("model_call_id") or "") for item in calls],
        "context": _step_context(record),
    }


@router.post("/api/v1/runs/{run_id}/tasks/{task_id}/steps/{step_name}/run", status_code=202)
def v1_run_step(
    run_id: str, task_id: str, step_name: str, request: V1StepRunRequest,
    authorization: str | None = Header(default=None),
    _: None = Depends(_authorize),
) -> dict[str, Any]:
    """基于已落盘记录单步执行一个步骤或 Agent；写操作必须显式确认。"""
    _require_current_workflow(run_id)
    from workflow.engine.step_runner import StepRunner, StepRunnerError, StepRunRequest

    run_id, task_id, step_name = _safe_id(run_id), _safe_id(task_id), _safe_id(step_name)
    store = _store()
    # Capture the persisted lifecycle before taking the write lock. If status is derived
    # after this endpoint acquires the lock, _v1_detail correctly sees a locked worker and
    # would incorrectly reject this very single-step request as RUN_ACTIVE.
    status_before_lock = str(_v1_detail(run_id).get("status") or "")
    if request.continue_after and (store.run_dir(run_id) / "reports/run_failure.json").is_file():
        raise HTTPException(status_code=409, detail="批次存在运行级异常，请先使用 Run 重试恢复，不能绕过批次异常继续任务")

    def _status(_value: str) -> str:
        return status_before_lock

    actor = "bearer-token" if authorization else "local-user"
    with _RUN_LOCKS_GUARD:
        run_lock = _run_lock(run_id)
    if not run_lock.acquire(blocking=False):
        message = "该执行正在被启动或重跑占用，请稍后重试"
        _audit_control(run_id, task_id, f"step.run.rejected:{step_name}", actor, message)
        raise HTTPException(status_code=409, detail={"code": "RUN_LOCKED", "message": message})
    def _continue(selected: StepRunRequest, payload: dict[str, Any]) -> None:
        from workflow.engine.entrypoint import _write_run_report_if_terminal
        from infrastructure.llm.config import load_config, workflow_recursion_limit
        from workflow.engine.wiring import (
            build_graph,
            build_services,
            continue_task_after_step,
        )
        runtime = load_config()
        services = build_services(config=runtime, source_repo=runtime.source_repo)
        lifecycle = LifecycleStore(store.root)
        lifecycle.update_run(run_id, status="running", worker_active=True, error={}, allow_reactivate=True)
        stop, heartbeat = _start_lifecycle_heartbeat(run_id)
        try:
            previous_report = store.run_dir(run_id) / "reports/final.json"
            if previous_report.is_file():
                archive = previous_report.parent / "history" / ("before-step-" + uuid.uuid4().hex + ".json")
                archive.parent.mkdir(parents=True, exist_ok=True)
                previous_report.replace(archive)
            continue_task_after_step(store, run_id, task_id, selected.step_name, payload, runtime, services)
            parent = build_graph(store=store, services=services, config=runtime)
            cfg = {"configurable": {"thread_id": run_id}, "recursion_limit": workflow_recursion_limit(
                max_attempts=runtime.max_attempts, task_count_hint=100)}
            snapshot = parent.get_state(cfg)
            final_state = dict(snapshot.values or {})
            if snapshot.next and final_state.get("task_id") == task_id:
                final_state = parent.invoke(None, config=cfg)
            _write_run_report_if_terminal(store, run_id, parent, final_state=final_state)
            _complete_run_lifecycle(run_id, store)
        except Exception as error:
            _record_background_failure(run_id, error, phase="workflow_resume")
            raise
        finally:
            stop.set()
            heartbeat.join(timeout=1)
            lifecycle.update_run(run_id, worker_active=False)

    runner = StepRunner(store, run_status=_status, on_finish=run_lock.release, on_continue=_continue)
    try:
        selected = StepRunRequest(run_id=run_id, task_id=task_id, step_name=step_name,
                                  confirm_write=request.confirm_write, continue_after=request.continue_after)
        runner.validate(selected)
        if request.continue_after:
            connection = _control_db()
            try:
                connection.execute("UPDATE run_control SET pause_requested=0 WHERE run_id=? AND cancel_requested=0", (run_id,))
                connection.commit()
            finally:
                connection.close()
        result = runner.run(selected)
    except StepRunnerError as exc:
        run_lock.release()
        _audit_control(run_id, task_id, f"step.run.rejected:{step_name}", actor, str(exc))
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except Exception:
        run_lock.release()
        raise
    _audit_control(
        run_id, task_id, f"step.run.requested:{step_name}", actor,
        f"confirm_write={request.confirm_write}",
    )
    return result


@router.get("/api/v1/runs/{run_id}/tasks/{task_id}/agent-calls/{model_call_id}")
def v1_agent_call(
    run_id: str, task_id: str, model_call_id: str, _: None = Depends(_authorize),
) -> dict[str, Any]:
    """单次 Agent 调用详情：调用元数据、提示词快照与工具事件。"""
    run_id, task_id, model_call_id = _safe_id(run_id), _safe_id(task_id), _safe_id(model_call_id)
    store = _store()
    run_dir = store.run_dir(run_id)
    if not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="运行记录不存在")
    call = next(
        (item for item in _load_model_calls(run_id, task_id)
         if str(item.get("model_call_id") or "") == model_call_id),
        None,
    )
    if call is None:
        raise HTTPException(status_code=404, detail="模型调用记录不存在")
    snapshot = _read_json(
        run_dir / "tasks" / task_id / "audit" / "prompts" / f"{model_call_id}.json", {},
    )
    events: list[dict[str, Any]] = []
    event_path = run_dir / "tasks" / task_id / "audit" / "agent_events.jsonl"
    if event_path.is_file():
        with event_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                if str(item.get("model_call_id") or "") != model_call_id:
                    continue
                if isinstance(item.get("event"), dict):
                    events.append(item["event"])
    return {
        "run_id": run_id,
        "task_id": task_id,
        "call": call,
        "prompt_snapshot": snapshot if isinstance(snapshot, dict) else {},
        "prompt_snapshot_available": bool(isinstance(snapshot, dict) and snapshot),
        "events": events,
        "event_total": len(events),
    }

