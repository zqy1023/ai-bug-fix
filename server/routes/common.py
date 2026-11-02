"""生产工作流工作台 API。

默认建议只监听 127.0.0.1。远程部署必须设置 WORKBENCH_API_TOKEN 并使用 HTTPS 反向代理。
API 只展示运行时记录，不把源码工作区或任意文件系统路径暴露给浏览器。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from core.policy import APPROVAL_RETRY_TARGETS, RetryTarget
from infrastructure.storage.lifecycle import LifecycleStore
from infrastructure.storage.store import RecordStore
from infrastructure.storage.redaction import public_exception
from config.settings import settings
from server.projections import run_error as project_run_error
from server.projections import run_timestamps, task_stages
from server.view_models import delivery_view, run_actions, task_retry_action

from config.nacos import nacos_get
from server.context import (
    _ID_PATTERN,
    _RUN_LOCKS,
    _RUN_LOCKS_GUARD,
    _STARTED_RUNS,
    _authorize,
    _config,
    _parse_time,
    _read_json,
    _require_current_workflow,
    _run_lock,
    _safe_id,
    _store,
)
SERVICE_NAME = settings.service_name
SERVICE_VERSION = settings.service_version

logger = logging.getLogger("server.api")


class ApprovalDecision(BaseModel):
    # decision：交付审批决定。
    decision: str
    # reason：人工操作说明，用于审计与终态报告。
    reason: str = ""
    # retry_target：驳回后必须明确指定的工作流阶段。
    retry_target: str | None = None


def _has_pending_approval_interrupt(store: RecordStore, run_id: str) -> bool:
    """判断心跳失效的 Run 是否仍有可恢复的审批断点。"""
    try:
        from workflow.engine.graph import build_graph

        snapshot = build_graph(store=store).get_state(
            {"configurable": {"thread_id": run_id}}
        )
        next_nodes = [str(item) for item in (getattr(snapshot, "next", ()) or ())]
        if not next_nodes:
            return False
        return bool(getattr(snapshot, "interrupts", ()) or ()) or any(
            bool(getattr(task, "interrupts", ()))
            for task in (getattr(snapshot, "tasks", ()) or ())
        )
    except Exception:
        logger.warning("无法判断心跳失效 Run 的审批断点 run_id=%s", run_id, exc_info=True)
        return False


def _run_payload(store: RecordStore, run_id: str) -> dict[str, Any]:
    import sys
    _mod = sys.modules.get("server.api") or sys.modules.get("server.routes")
    if _mod and "_run_payload" in _mod.__dict__ and _mod.__dict__["_run_payload"] is not _run_payload:
        return _mod.__dict__["_run_payload"](store, run_id)
    directory = store.run_dir(run_id)
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail="运行记录不存在")
    from core.contracts import WORKFLOW_VERSION
    if store.load_run(run_id).get("workflow_version") != WORKFLOW_VERSION:
        raise HTTPException(status_code=409, detail="工作流版本不受支持，请新建当前版本执行")
    # 任务板一次请求要投影整个执行：只读一次 steps.json 并在所有任务间复用。
    # 每个任务各读一遍既浪费 208KB 的解析，也把每次"读撞上写"的窗口乘以任务数。
    steps_payload = store.steps_snapshot(run_id)
    run_steps = [
        item.model_dump(mode="json")
        for item in store.list_steps_from_snapshot(run_id, steps_payload)
    ]
    from server.projections import task_catalog, task_counts
    task_slots, dispatched_count = task_catalog(steps_payload, directory)
    lifecycle_store = LifecycleStore(store.root)
    ic_tasks = steps_payload.get("identify_carrier", {}).get("output", {}).get("tasks", [])
    identify_by_id = {
        str(t.get("task_id") or ""): dict(t.get("problem") or {})
        for t in ic_tasks
        if isinstance(t, dict) and t.get("task_id")
    }
    norm_records = _read_json(directory / "artifacts" / "normalized_records.json", {}).get("records", [])
    norm_by_carrier_biz = {
        (str(r.get("carrier") or ""), str(r.get("biztype") or "")): dict(r.get("problem") or {})
        for r in norm_records
        if isinstance(r, dict) and (r.get("carrier") or r.get("biztype"))
    }
    tasks: list[dict[str, Any]] = []
    if isinstance(task_slots, list):
        for slot in task_slots:
            if not isinstance(slot, dict) or not isinstance(slot.get("task_id"), str):
                continue
            task_id = slot["task_id"]
            task_steps = [
                item.model_dump(mode="json")
                for item in store.list_steps_from_snapshot(run_id, steps_payload, task_id)
            ]
            final_path = store.task_dir(run_id, task_id) / "reports" / "final.json"
            report = _read_json(final_path, {})
            # 同一任务可经历多轮回修与多次审批；必须以最后一次 request_approval 为准。
            # 读取第一条会让旧的已完成审批遮蔽当前 running/PENDING 的 interrupt，
            # API 随后按最新审批记录恢复 LangGraph checkpoint。
            approval = _latest_approval_step(task_steps)
            carrier = str((report or {}).get("carrier") or _task_identity(slot, "carrier"))
            biztype = str((report or {}).get("biztype") or _task_identity(slot, "biztype"))
            task_lifecycle = lifecycle_store.read_task(run_id, task_id)
            derived_step_status = _task_status(task_steps, approval)
            lifecycle_status = str(task_lifecycle.get("status") or "").lower()
            task_status_labels = {
                "pending": "PENDING", "queued": "PENDING", "running": "RUNNING",
                "retrying": "RETRYING", "waiting_approval": "WAITING_APPROVAL",
                "paused": "PAUSED", "succeeded": "SUCCESS", "partial_failed": "PARTIAL_FAILED",
                "failed": "FAILED", "cancelled": "CANCELLED", "manual": "MANUAL", "blocked": "BLOCKED",
                "skipped": "SKIPPED",
            }
            if not isinstance(report, dict):
                raise HTTPException(status_code=500, detail="当前终态报告格式无效")
            approval_pending = _approval_pending(approval) or lifecycle_status == "waiting_approval"
            active_derived_statuses = {"RUNNING", "PENDING", "WAITING_APPROVAL", "PAUSED"}
            task_status = (
                task_status_labels[lifecycle_status]
                if lifecycle_status in task_status_labels
                else derived_step_status
                if derived_step_status in active_derived_statuses
                else str(report.get("result") or derived_step_status).upper()
            )
            # 终态任务不可能是“等待审批”：失败/取消任务残留的旧 interrupt
            # 只会让任务在列表和审批中心同时出现，必须按最终状态收敛。
            approval_pending = approval_pending and task_status == "WAITING_APPROVAL"
            task_prob = {**dict(_task_problem(slot)), **dict((slot.get("identity") or {}).get("problem") or {})}
            if not task_prob.get("message") and task_id in identify_by_id:
                for k, v in identify_by_id[task_id].items():
                    task_prob.setdefault(k, v)
            if not task_prob.get("message"):
                norm_hit = norm_by_carrier_biz.get((carrier, biztype))
                if norm_hit:
                    for k, v in norm_hit.items():
                        task_prob.setdefault(k, v)
            tasks.append({
                "task_id": task_id, "carrier": carrier, "biztype": biztype,
                "status": task_status,
                "problem": task_prob, "steps": task_steps,
                "report": report, "approval_pending": approval_pending,
                "lifecycle": task_lifecycle,
            })
    pending = sum(1 for task in tasks if task["approval_pending"])
    results = [str(task["status"]).upper() for task in tasks]
    created = datetime.fromtimestamp(directory.stat().st_ctime, tz=timezone.utc).isoformat()
    updated = datetime.fromtimestamp(directory.stat().st_mtime, tz=timezone.utc).isoformat()
    run_meta = _read_json(directory / "run.json", {})
    run_failure = _read_json(directory / "reports" / "run_failure.json", {})
    run_report = _read_json(directory / "reports" / "final.json", {})
    identified_tasks = _identified_task_total(run_steps)
    finalized = any(
        step.get("name") == "finalize_execution" and step.get("status") == "succeeded"
        for step in run_steps
    )
    run_lifecycle = lifecycle_store.read_run(run_id)
    lifecycle_status = str(run_lifecycle.get("status") or "").lower()
    run_lock = _RUN_LOCKS.get(run_id)
    lock_active = bool(run_lock and run_lock.locked())
    heartbeat_at = _parse_time(run_lifecycle.get("heartbeat_at"))
    heartbeat_fresh = bool(heartbeat_at and (datetime.now(timezone.utc) - heartbeat_at).total_seconds() <= 30)
    worker_active = lock_active or (
        bool(run_lifecycle.get("worker_active")) and heartbeat_fresh
    )

    if lifecycle_status == "waiting_approval":
        # 人工审批已移除：历史等待审批的 Run 不能继续占用全局串行位，
        # 也不能让任务永久停在 WAITING_APPROVAL。
        removed_reason = "人工审批已移除，历史待审批任务已结束；如需继续请重新创建批次。"
        lifecycle_store.update_run(
            run_id, status="failed", phase="request_approval",
            error_type="APPROVAL_REMOVED", error_message=removed_reason,
            terminal_reason=removed_reason,
            next_action="重新创建批次并等待 AI 审查与自动交付。",
        )
        run_lifecycle = lifecycle_store.read_run(run_id)
        lifecycle_status = "failed"
        run_failure = {
            "status": "FAILED", "error_type": "APPROVAL_REMOVED",
            "message": removed_reason, "terminal_step": "request_approval",
            "terminal_reason": removed_reason,
            "next_action": "重新创建批次并等待 AI 审查与自动交付。",
        }
        worker_active = False
        _settle_task_lifecycles_after_run_failure(
            run_id, store, phase="approval_removed", reason=removed_reason,
            next_action="重新创建批次并等待 AI 审查与自动交付。",
        )
        for task in tasks:
            current = lifecycle_store.read_task(run_id, task["task_id"])
            task["lifecycle"] = current
            task["status"] = str(current.get("status") or task["status"]).upper()
            task["approval_pending"] = False
        results = [str(task["status"]).upper() for task in tasks]

    if not worker_active and (run_failure or run_report) and lifecycle_status in {"", "failed", "partial_failed", "succeeded", "cancelled"}:
        if any(task["status"] in {"RUNNING", "RETRYING", "PENDING", "WAITING_APPROVAL"} for task in tasks):
            report = run_failure or run_report
            _settle_task_lifecycles_after_run_failure(
                run_id, store, phase=str(report.get("terminal_step") or "workflow_exit"),
                reason=str(report.get("message") or report.get("terminal_reason") or "本轮工作流已退出，任务没有完成收尾。"),
                next_action=str(report.get("next_action") or "检查任务失败节点后重试。"),
            )
            for task in tasks:
                current = lifecycle_store.read_task(run_id, task["task_id"])
                if current:
                    task["lifecycle"] = current
                    task["status"] = str(current.get("status") or task["status"]).upper()
                    task["approval_pending"] = task["status"] == "WAITING_APPROVAL"
            results = [str(task["status"]).upper() for task in tasks]

    if lifecycle_status in {"queued", "running", "retrying", "pause_requested", "cancel_requested"} and not worker_active:
        waiting_approval = any(task["approval_pending"] for task in tasks)
        if waiting_approval and _has_pending_approval_interrupt(store, run_id):
            # 进程退出但审批断点仍可恢复：保留待审批状态，允许人工继续，
            # 不能把它误写成“心跳失败”而关闭审批入口。
            resume_reason = "后台工作线程已退出，但仍有可恢复的审批断点；请完成审批或从 Run 恢复。"
            lifecycle_store.update_run(
                run_id, status="waiting_approval", phase="request_approval",
                current_step="request_approval",
                error_type="WORKER_HEARTBEAT_TIMEOUT", error_message=resume_reason,
                terminal_reason="",
                next_action="在交付审批页完成决策，或从 Run 控制栏恢复执行。",
            )
            run_lifecycle = lifecycle_store.read_run(run_id)
            lifecycle_status = "waiting_approval"
            worker_active = False
        else:
            # A crashed process must not leave an active badge forever.
            stale_reason = "后台工作线程心跳已超时，执行不再被视为活动状态。请检查失败节点后重试。"
            lifecycle_store.update_run(
                run_id, status="failed", phase=str(run_lifecycle.get("phase") or "worker"),
                error_type="WORKER_HEARTBEAT_TIMEOUT", error_message=stale_reason,
                terminal_reason=stale_reason,
                next_action="检查失败节点；可从 Run 控制栏重试，或在任务详情单步重试。",
            )
            run_lifecycle = lifecycle_store.read_run(run_id)
            lifecycle_status = "failed"
            run_failure = run_failure or {
                "status": "FAILED", "error_type": "WORKER_HEARTBEAT_TIMEOUT",
                "message": stale_reason, "terminal_step": str(run_lifecycle.get("phase") or "worker"),
                "terminal_reason": stale_reason,
                "next_action": "检查失败节点；可从 Run 控制栏重试，或在任务详情单步重试。",
            }
            worker_active = False
            _settle_task_lifecycles_after_run_failure(
                run_id, store, phase="worker_timeout", reason=stale_reason,
                next_action="检查后台异常后使用 Run 级重试恢复。",
            )
            for task in tasks:
                current = lifecycle_store.read_task(run_id, task["task_id"])
                task["lifecycle"] = current
                if current.get("status") == "failed":
                    task["status"] = "FAILED"
                    task["approval_pending"] = False
            results = [str(task["status"]).upper() for task in tasks]

    lifecycle_to_run = {
        "queued": "queued", "running": "running", "retrying": "running",
        "pause_requested": "pause_requested", "paused": "paused",
        "waiting_approval": "waiting_approval", "cancel_requested": "cancel_requested",
        "succeeded": "succeeded", "partial_failed": "partial_failed",
        "failed": "failed", "cancelled": "cancelled", "manual": "partial_failed", "blocked": "partial_failed",
    }
    # A terminal lifecycle must not be shown as running just because the worker
    # still holds its lock while flushing failure artifacts and releasing resources.
    if worker_active:
        # If the worker is still unwinding an exception, keep controls usable and the
        # Run non-terminal until the worker has released its lease.
        status = "running"
    elif lifecycle_status in {"succeeded", "partial_failed", "failed", "cancelled", "manual", "blocked"}:
        status = lifecycle_to_run[lifecycle_status]
    elif lifecycle_status in lifecycle_to_run:
        status = lifecycle_to_run[lifecycle_status]
    elif isinstance(run_failure, dict) and run_failure:
        status = "failed"
    elif pending:
        status = "waiting_approval"
    else:
        status = _derive_run_status(
            run_steps, tasks,
            run_report=run_report if isinstance(run_report, dict) else {},
            startup_state=str((run_meta or {}).get("startup_state") or ""),
            finalized=finalized,
        )
    terminal_statuses = {"succeeded", "partial_failed", "failed", "cancelled"}
    terminal = (lifecycle_status in terminal_statuses) if lifecycle_status else (bool(run_failure) or bool(run_report) or finalized)
    if worker_active or status in {"queued", "running", "pause_requested", "cancel_requested", "waiting_approval", "paused"}:
        terminal = False
    return {
        "run_id": run_id,
        "status": status,
        "terminal": terminal,
        "worker_active": worker_active,
        "phase": str(run_lifecycle.get("phase") or run_lifecycle.get("current_step") or ""),
        "run_lifecycle": run_lifecycle,
        "identified_tasks": identified_tasks,
        "dispatched_tasks": dispatched_count,
        "run_error": project_run_error(run_failure if isinstance(run_failure, dict) else {}, run_lifecycle),
        **run_timestamps(run_lifecycle, run_report, run_failure, created_at=created, terminal=terminal),
        "created_at": created,
        "updated_at": updated,
        "steps": run_steps,
        "tasks": tasks,
        "summary": task_counts(tasks, run_steps),
    }


def _task_identity(slot: dict[str, Any], key: str) -> str:
    for value in slot.values():
        if not isinstance(value, dict):
            continue
        candidate = value.get(key)
        if candidate:
            return str(candidate)
        for nested_key in ("input", "output", "result"):
            nested = value.get(nested_key)
            if isinstance(nested, dict) and nested.get(key):
                return str(nested[key])
    return ""


def _task_problem(slot: dict[str, Any]) -> dict[str, Any]:
    for value in slot.values():
        if isinstance(value, dict) and isinstance(value.get("input"), dict):
            problem = value["input"].get("problem")
            if isinstance(problem, dict) and problem:
                return problem
    return {}


def _task_status(steps: list[dict[str, Any]], approval: dict[str, Any] | None) -> str:
    """活动状态优先于旧失败记录；只有终态收尾或停滞后才报告任务失败。"""
    if _approval_pending(approval):
        return "WAITING_APPROVAL"
    if any(s.get("status") == "running" for s in steps):
        return "RUNNING"
    finalizer = next((
        step for step in reversed(steps)
        if step.get("name") == "finalize_execution"
        and step.get("status") in {"succeeded", "skipped", "failed"}
    ), None)
    if finalizer:
        output = finalizer.get("output") if isinstance(finalizer.get("output"), dict) else {}
        result = str(output.get("delivery_status") or finalizer.get("result") or "").upper()
        if result in {"CHANGED_DELIVERED", "UNCHANGED_DELIVERED", "SKIPPED"}:
            return result
        if result in {"SUCCESS", "SUCCEEDED", "COMPLETED", "DELIVERED"}:
            return "COMPLETED"
        if result in {"MANUAL", "INSUFFICIENT_EVIDENCE", "MANUAL_REVIEW_REQUIRED"}:
            return "MANUAL"
        if result == "BLOCKED":
            return "BLOCKED"
        if result == "CANCELLED":
            return "CANCELLED"
        if result in {"FAILED", "FAIL", "REJECTED"} or finalizer.get("status") == "failed":
            return "FAILED"
        return "COMPLETED" if finalizer.get("status") in {"succeeded", "skipped"} else "FAILED"
    if any(s.get("status") == "waiting" for s in steps):
        return "PAUSED"
    if any(s.get("status") == "failed" for s in steps):
        return "FAILED"
    return "RUNNING" if steps else "PENDING"


def _latest_approval_step(steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    """返回任务最近一次审批记录，不能让历史审批遮蔽当前 interrupt。"""
    return next(
        (step for step in reversed(steps) if isinstance(step, dict) and step.get("name") == "request_approval"),
        None,
    )


def _approval_pending(step: dict[str, Any] | None) -> bool:
    if not step:
        return False
    # LangGraph interrupt 落盘为 status=waiting，output 为空；旧逻辑只看
    # running 或 output.decision，导致真实等待审批的任务从审批中心消失。
    if step.get("status") in {"running", "waiting"}:
        return True
    output = step.get("output")
    if isinstance(output, dict) and output.get("decision") == "PENDING":
        return True
    input_payload = step.get("input")
    return isinstance(input_payload, dict) and input_payload.get("decision") == "PENDING"


def _identified_task_total(steps: list[dict[str, Any]]) -> int:
    """identify_carrier 识别出的候选任务数；用于区分“待派发”和“确实没有错误”。"""
    for step in steps:
        if step.get("name") != "identify_carrier" or step.get("status") != "succeeded":
            continue
        output = step.get("output") if isinstance(step.get("output"), dict) else {}
        tasks = output.get("tasks")
        if isinstance(tasks, dict):
            return len(tasks)
        if isinstance(tasks, list):
            return len(tasks)
        return 0
    return 0


def _status_from_report(run_report: dict[str, Any], tasks: list[dict[str, Any]]) -> str:
    """按落盘终态报告与任务结果收敛执行状态。"""
    results = [str(task["status"]).upper() for task in tasks]
    result = str(run_report.get("result") or run_report.get("status") or "").upper()
    failed = any(item in {"FAILED", "FAIL", "REJECTED"} for item in results)
    manual = any(item in {"MANUAL", "MANUAL_REVIEW_REQUIRED", "INSUFFICIENT_EVIDENCE"} for item in results)
    succeeded = any(item in {"SUCCESS", "SUCCEEDED", "COMPLETED", "DELIVERED"} for item in results)
    if result.startswith("CANCEL"):
        return "cancelled"
    if result in {"FAILED", "FAIL", "ERROR"}:
        return "failed"
    if manual:
        # 人工介入不是成功；避免把包含 MANUAL 任务的整轮执行误标为已完成。
        return "partial_failed"
    if failed:
        return "partial_failed" if succeeded else "failed"
    return "succeeded"


def _derive_run_status(
    steps: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    *,
    run_report: dict[str, Any] | None = None,
    startup_state: str = "",
    finalized: bool = False,
) -> str:
    """执行状态只由生命周期事实推导，不再用“无任务槽位”猜完成。

    identify_carrier 成功到 task_dispatch 写入第一个任务槽位之间存在窗口，
    该窗口内任务尚未派发但工作流已经运行，必须返回 running；只有终态报告
    （或 finalize_execution 成功）才算结束。
    """
    if not steps and not tasks:
        # 后台任务已接管但首条步骤尚未落盘：这是启动阶段（同步源码/拉取日志）。
        return "running" if startup_state == "started" else "queued"
    if run_report or finalized:
        return _status_from_report(run_report or {}, tasks)
    return "running"


def _all_run_ids(store: RecordStore) -> list[str]:
    root = store.root / "runs"
    if not root.is_dir():
        return []
    return sorted(
        (item.name for item in root.iterdir() if item.is_dir() and _ID_PATTERN.fullmatch(item.name)),
        key=lambda name: (root / name).stat().st_mtime,
        reverse=True,
    )


def _health_payload(_: None = Depends(_authorize)) -> dict[str, Any]:
    config = _config()
    from agents.review.contract import REVIEW_CONTRACT_VERSION
    from core.contracts import WORKFLOW_VERSION
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "workflow_version": WORKFLOW_VERSION,
        "review_contract_version": REVIEW_CONTRACT_VERSION,
        # 该字段只表示「日志行本身是否脱敏」，当前进程日志不脱敏，因此必须如实
        # 报告 False；交付面与异常摘要的脱敏走 redact_for_delivery /
        # public_exception，与这个开关无关，不能据此把它改成 True。
        "log_redaction_enabled": False,
        "runtime_dir_configured": bool(config.runtime_dir),
        "source_repo_configured": config.source_repo is not None,
        "api_token_required": bool(nacos_get("WORKBENCH_API_TOKEN", "").strip()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _pending_approvals(_: None = Depends(_authorize)) -> dict[str, Any]:
    import sys
    _mod = sys.modules.get("server.api") or sys.modules.get("server.routes")
    if _mod and "_pending_approvals" in _mod.__dict__ and _mod.__dict__["_pending_approvals"] is not _pending_approvals:
        return _mod.__dict__["_pending_approvals"](_)
    store = _store()
    approvals = []
    for run_id in _all_run_ids(store):
        try:
            run = _run_payload(store, run_id)
        except (HTTPException, OSError, ValueError):
            logger.warning("审批队列跳过无法读取的执行 run_id=%s", run_id, exc_info=True)
            continue
        for task in run["tasks"]:
            if task["approval_pending"]:
                approvals.append({
                    "run_id": run_id,
                    "task_id": task["task_id"],
                    "carrier": task["carrier"],
                    "biztype": task["biztype"],
                    "problem": task["problem"],
                    "updated_at": run["updated_at"],
                    "approval_step": _latest_approval_step(task["steps"]),
                })
    return {"items": approvals, "total": len(approvals)}


def _artifact_content(
    run_id: str,
    path: str = Query(..., min_length=1, max_length=500),
    task_id: str = "",
    _: None = Depends(_authorize),
) -> JSONResponse:
    store = _store()
    run_id = _safe_id(run_id)
    if not task_id:
        base = store.run_dir(run_id).resolve()
    else:
        _safe_id(task_id)
        base = store.task_dir(run_id, task_id).resolve()
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(status_code=400, detail="非法产物路径")
    if path == "metrics/agent.jsonl":
        return JSONResponse(content={"path": path, "task_id": task_id,
                                     "content": store.read_metrics(run_id, task_id)})
    target = (base / relative).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        raise HTTPException(status_code=404, detail="产物不存在")
    if target.stat().st_size > 2 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="产物超过 2MB，请使用本地文件工具检查")
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        value = target.read_text(encoding="utf-8", errors="replace")
    return JSONResponse(content={"path": path, "task_id": task_id, "content": value})




def _submit_approval(
    run_id: str,
    task_id: str,
    request: ApprovalDecision,
    background: BackgroundTasks,
    _: None = Depends(_authorize),
) -> dict[str, Any]:
    _require_current_workflow(run_id)
    decision = request.decision.upper()
    if decision not in {"APPROVE", "REJECT"}:
        raise HTTPException(status_code=422, detail="变更审批 decision 只能是 APPROVE 或 REJECT")
    if decision == "REJECT" and not request.retry_target:
        raise HTTPException(status_code=422, detail="驳回必须明确指定回退节点")
    if decision == "REJECT" and request.retry_target not in APPROVAL_RETRY_TARGETS:
        raise HTTPException(status_code=422, detail="无效的驳回回退节点")
    run_id, task_id = _safe_id(run_id), _safe_id(task_id)
    store = _store()
    run = _run_payload(store, run_id)
    task = next((item for item in run["tasks"] if item["task_id"] == task_id), None)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    steps = task.get("steps") if isinstance(task.get("steps"), list) else []
    approval_step = _latest_approval_step(steps)
    approval_input = approval_step.get("input") if isinstance(approval_step, dict) and isinstance(approval_step.get("input"), dict) else {}
    if not task.get("approval_pending"):
        raise HTTPException(status_code=409, detail="该任务当前没有可恢复的人工决策中断；历史终态任务不能原地续跑")

    with _RUN_LOCKS_GUARD:
        lock = _run_lock(run_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该 run 正在执行恢复操作")
    worker_lease: tuple[str, str] | None = None
    try:
        worker_lease = _claim_worker_lease(run_id)
        fingerprint = str(approval_input.get("approval_fingerprint") or "")
        resume_payload = {"decision": decision, "approval_fingerprint": fingerprint, "task_id": task_id}
        if decision == "REJECT":
            resume_payload.update({
                "reason": str(request.reason or "").strip()[:1000],
                "retry_target": str(request.retry_target),
            })
        # Transfer lock ownership only after the background task is enqueued.
        background.add_task(_resume_workflow_background, run_id, lock, resume_payload, worker_lease)
    except BaseException:
        if worker_lease is not None:
            _release_worker_lease(run_id, worker_lease)
        lock.release()
        raise
    return {"run_id": run_id, "task_id": task_id, "decision": decision, "status": "resume_requested"}

# Versioned control-plane contract consumed by frontend/src/lib/api.ts.


class V1StartRunRequest(BaseModel):
    model_config = {"extra": "forbid", "validate_default": True}
    # log_hours：本轮滚动回溯的小时数。
    log_hours: int = Field(default=settings.default_log_hours, ge=1, le=720)
    # log_limit：本轮最多读取的日志数；0 表示全量拉取，不设置条数上限。
    log_limit: int = Field(default=settings.default_log_limit, ge=0)
    # review_mode：agent 表示 AI 审查，human 表示强制人工审查。
    # 人工审批已移除，审查模式固定为 AI 自动审查。
    review_mode: Literal["agent"] = "agent"
    # max_attempts：单任务修复尝试总次数，包含首次执行。
    max_attempts: int = Field(
        default=settings.default_max_attempts,
        ge=1,
        le=5,
    )
    # agent_policy：可选批次级 Agent 模型及策略配置。
    agent_policy: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def apply_runtime_defaults(cls, value: Any) -> Any:
        """只补齐未传入的字段，使 API 与 CLI 遵循同一环境配置。"""
        if isinstance(value, dict):
            return {**_config().run_parameters(), **value}
        return value




class V1ApprovalDecision(BaseModel):
    # decision：交付审批决定。
    decision: Literal["approve", "reject"]
    # reason：审批说明或拒绝原因，最多 1000 字符。
    reason: str = Field(default="", max_length=1000)
    # 驳回时明确指定重跑阶段；自定义原因必须由 UI 明确选择，服务端再次白名单校验。
    retry_target: RetryTarget | None = None


def _v1_status(value: str) -> str:
    status = (value or "").lower()
    aliases = {"pending": "queued", "accepted": "queued", "completed": "succeeded", "success": "succeeded"}
    return aliases.get(status, status if status in {
        "queued", "running", "waiting_approval", "succeeded", "partial_failed", "failed", "paused",
        "pause_requested", "cancelled", "cancel_requested",
    } else "failed")


def _v1_run_summary(payload: dict[str, Any]) -> dict[str, Any]:
    import sys
    _mod = sys.modules.get("server.api") or sys.modules.get("server.routes")
    if _mod and "_v1_run_summary" in _mod.__dict__ and _mod.__dict__["_v1_run_summary"] is not _v1_run_summary:
        return _mod.__dict__["_v1_run_summary"](payload)
    summary = payload.get("summary") or {}
    created = payload.get("started_at") or payload.get("created_at") or payload.get("updated_at")
    run_id = str(payload["run_id"])
    status = _v1_status(str(payload.get("status") or ""))
    try:
        connection = _control_db()
        try:
            control = connection.execute(
                "SELECT pause_requested,cancel_requested FROM run_control WHERE run_id=?", (run_id,)
            ).fetchone()
        finally:
            connection.close()
        paused_step = any(
            step.get("status") == "waiting"
            for step in list(payload.get("steps") or [])
            + [item for task in (payload.get("tasks") or []) for item in (task.get("steps") or [])]
        )
        # 控制标记与运行记录可能短暂不一致：只有运行已经落盘 cancelled 才展示已停止，
        # 否则保持 cancel_requested，避免前端误判为可写状态。
        if control and control["cancel_requested"] and status not in {
            "succeeded", "failed", "partial_failed", "cancelled",
        }:
            status = "cancel_requested"
        elif control and control["pause_requested"]:
            status = "paused" if paused_step or (status == "paused" and not payload.get("worker_active")) else "pause_requested"
    except Exception:
        logger.warning("读取执行控制状态失败 run_id=%s", run_id, exc_info=True)
    return {
        "run_id": run_id, "status": status,
        "terminal": bool(payload.get("terminal")) and not bool(payload.get("worker_active")),
        "worker_active": bool(payload.get("worker_active")),
        "phase": str(payload.get("phase") or ""),
        "run_lifecycle": payload.get("run_lifecycle") if isinstance(payload.get("run_lifecycle"), dict) else {},
        "identified_tasks": int(payload.get("identified_tasks") or 0),
        "dispatched_tasks": int(payload.get("dispatched_tasks") or 0),
        "started_at": created, "finished_at": payload.get("finished_at"),
        "task_count": int(summary.get("tasks_total") or len(payload.get("tasks") or [])),
        "succeeded_tasks": int(summary.get("tasks_success") or 0),
        "changed_delivered_tasks": int(summary.get("tasks_changed_delivered") or 0),
        "unchanged_delivered_tasks": int(summary.get("tasks_unchanged_delivered") or 0),
        "failed_tasks": int(summary.get("tasks_failed") or 0),
        "blocked_tasks": int(summary.get("tasks_blocked") or 0),
        "manual_tasks": int(summary.get("tasks_manual") or 0),
        "waiting_tasks": int(summary.get("tasks_waiting_approval") or 0),
        "inactive_tasks": int(summary.get("tasks_inactive") or 0),
        "pending_tasks": int(summary.get("tasks_pending") or 0),
        "running_tasks": int(summary.get("tasks_running") or 0),
        "skipped_tasks": int(summary.get("tasks_skipped") or 0),
        "cancelled_tasks": int(summary.get("tasks_cancelled") or 0),
        "completed_tasks": int(summary.get("tasks_completed") or 0),
        "operator": payload.get("operator") or "系统", "created_at": created, "updated_at": payload.get("updated_at") or created,
    }


def _v1_detail(run_id: str) -> dict[str, Any]:
    import sys
    _mod = sys.modules.get("server.api") or sys.modules.get("server.routes")
    if _mod and "_v1_detail" in _mod.__dict__ and _mod.__dict__["_v1_detail"] is not _v1_detail:
        return _mod.__dict__["_v1_detail"](run_id)
    store = _store()
    payload = _run_payload(store, _safe_id(run_id))
    run_meta = _read_json(store.run_dir(run_id) / "run.json", {})
    summary = _v1_run_summary(payload)
    steps = list(payload.get("steps") or [])
    tasks, artifacts = [], []
    for step in steps:
        for ref in step.get("artifacts") or []:
            artifacts.append({
                "name": ref.get("name") or "artifact", "path": ref.get("path") or "",
                "media_type": ref.get("media_type") or "application/json",
                "description": step.get("name") or "", "size_bytes": None,
            })
    for task in payload.get("tasks") or []:
        from core.task_outcome import task_outcome
        result = task_outcome(task)
        report = task.get("report") if isinstance(task.get("report"), dict) else {}
        task_steps = task.get("steps") or []
        tasks.append({
            "task_id": task.get("task_id", ""), "carrier": task.get("carrier", ""),
            "biztype": task.get("biztype", ""), "result": result, "terminal_status": result.lower(),
            "terminal_step": str(report.get("terminal_step") or ""),
            "terminal_reason": str(report.get("terminal_reason") or ""),
            "next_action": str(report.get("next_action") or ""),
            "lifecycle": task.get("lifecycle") if isinstance(task.get("lifecycle"), dict) else {},
            "current_step": str((task.get("lifecycle") or {}).get("current_step") or ""),
            "attempt": int((task.get("lifecycle") or {}).get("attempt") or 0),
            "steps_count": len(task_steps),
            "duration_ms": report.get("elapsed_ms"),
            "approval_pending": bool(task.get("approval_pending")),
        })
    resumable, next_nodes = False, []
    checkpoint_id: str | None = None
    checkpoint_created_at: str | None = None
    checkpoint_node: str | None = None
    checkpoint_parent_id: str | None = None
    checkpoint_has_interrupt = False
    checkpoint_read_failed = False
    try:
        from workflow.engine.graph import build_graph
        graph = build_graph(store=store)
        snapshot = graph.get_state({"configurable": {"thread_id": run_id}})
        next_nodes = [str(item) for item in (getattr(snapshot, "next", ()) or ())]
        resumable = bool(next_nodes) and not any(task.get("approval_pending") for task in payload.get("tasks") or [])
        checkpoint_config = getattr(snapshot, "config", {}) or {}
        configurable = checkpoint_config.get("configurable", {}) if isinstance(checkpoint_config, dict) else {}
        if isinstance(configurable, dict):
            checkpoint_id = str(configurable.get("checkpoint_id") or "") or None
        checkpoint_created_at = str(getattr(snapshot, "created_at", None) or "") or None
        checkpoint_node = next_nodes[0] if next_nodes else None
        parent_config = getattr(snapshot, "parent_config", None) or {}
        parent_configurable = parent_config.get("configurable", {}) if isinstance(parent_config, dict) else {}
        if isinstance(parent_configurable, dict):
            checkpoint_parent_id = str(parent_configurable.get("checkpoint_id") or "") or None
        checkpoint_has_interrupt = bool(getattr(snapshot, "interrupts", ()) or ()) or any(
            bool(getattr(task, "interrupts", ())) for task in (getattr(snapshot, "tasks", ()) or ())
        )
    except Exception:
        # checkpoint 读不到时不能把空 next_nodes 当成"没有后续节点"：那会把
        # 仍在恢复窗口里的 Run 误判成终态 cancelled。这里只标注降级，不改
        # resumable——读不到 checkpoint 的"恢复"必然再次失败，还会先删掉
        # 上一轮报告，不能把它当成可用动作提供给调用方。
        checkpoint_read_failed = True
        logger.warning("无法读取执行 checkpoint run_id=%s", run_id, exc_info=True)
    status = _v1_status(str(summary.get("status") or payload.get("status") or ""))
    connection = _control_db()
    cancel_unconfirmed = False
    try:
        row = connection.execute(
            "SELECT pause_requested,cancel_requested FROM run_control WHERE run_id=?", (run_id,)
        ).fetchone()
        terminal_run_statuses = {"succeeded", "partial_failed", "failed", "cancelled"}
        # Stale control flags must not overwrite an already settled Run result.
        if row and row["cancel_requested"] and status not in terminal_run_statuses:
            if checkpoint_read_failed:
                # 无法确认 checkpoint 是否还有后续节点时，只展示"停止中"，
                # 不落成终态 cancelled。
                status = "cancel_requested"
                cancel_unconfirmed = True
            else:
                status = "cancelled" if not next_nodes and not summary.get("worker_active") else "cancel_requested"
        elif row and row["pause_requested"] and status not in terminal_run_statuses:
            has_waiting_step = any(step.get("status") == "waiting" for step in steps)
            has_waiting_step = has_waiting_step or any(
                step.get("status") == "waiting"
                for task in (payload.get("tasks") or []) for step in (task.get("steps") or [])
            )
            status = "paused" if has_waiting_step or (status == "paused" and not summary.get("worker_active")) else "pause_requested"
    finally:
        connection.close()
    run_report = _read_json(store.run_dir(run_id) / "reports" / "final.json", {})
    if isinstance(run_report, dict) and run_report:
        artifacts.append({
            "name": "本轮终态报告", "path": "reports/final.json",
            "media_type": "application/json", "description": "Run 级终态总结",
            "size_bytes": None,
        })
    # 只有“报告已落盘且图无后续节点”才算终态；等待审批、过渡窗口都继续推流。
    terminal = bool(summary.get("terminal")) and not bool(summary.get("worker_active"))
    if payload.get("run_error") and not summary.get("worker_active"):
        terminal = True
    if status == "cancelled" and not summary.get("worker_active"):
        terminal = True
    if cancel_unconfirmed:
        # 取消标记存在但 checkpoint 读不到：不能宣称终态 cancelled，前端应
        # 继续按"停止中"展示并等待人工处理。
        terminal = False
    from core.contracts import WORKFLOW_VERSION
    current = run_meta.get("workflow_version") == WORKFLOW_VERSION
    controls = run_actions(
        current=current, status=status, worker_active=bool(summary.get("worker_active")),
        resumable=resumable, safe_startup=not payload.get("tasks") and
        {step.get("name") for step in steps}.issubset({"sync_source_repo"}),
    )
    return {
        **summary, "status": status, "terminal": terminal,
        "read_only": not current,
        "actions": {key: value.model_dump(mode="json") for key, value in controls.items()},
        "config": {
            **_config().run_parameters(run_meta),
        },
        "steps": steps, "tasks": tasks, "artifacts": artifacts,
        "run_report": run_report if isinstance(run_report, dict) else {},
        "run_error": payload.get("run_error") or {},
        "checkpoint": {
            "resumable": resumable, "next_nodes": next_nodes,
            "checkpoint_id": checkpoint_id, "created_at": checkpoint_created_at,
            "node": checkpoint_node, "parent_checkpoint_id": checkpoint_parent_id,
            "has_interrupt": checkpoint_has_interrupt,
            # 读取失败时前端/运维据此判断"不可恢复"是数据问题而非执行状态。
            "read_failed": checkpoint_read_failed,
        },
    }


def _checkpoint_projection(snapshot: Any) -> dict[str, Any]:
    """Expose checkpoint metadata only; workflow channel values stay server-side."""
    config = getattr(snapshot, "config", {}) or {}
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    parent_config = getattr(snapshot, "parent_config", None) or {}
    parent_configurable = parent_config.get("configurable", {}) if isinstance(parent_config, dict) else {}
    next_nodes = [str(item) for item in (getattr(snapshot, "next", ()) or ())]
    return {
        "checkpoint_id": str(configurable.get("checkpoint_id") or "") or None,
        "parent_checkpoint_id": str(parent_configurable.get("checkpoint_id") or "") or None,
        "created_at": str(getattr(snapshot, "created_at", None) or "") or None,
        "next_nodes": next_nodes,
        "node": next_nodes[0] if next_nodes else None,
        "has_interrupt": bool(getattr(snapshot, "interrupts", ()) or ()) or any(
            bool(getattr(task, "interrupts", ())) for task in (getattr(snapshot, "tasks", ()) or ())
        ),
    }










# 控制库连接、Schema 初始化和审计写入只由 server.control_store 维护。
from server.control_store import _audit_control, _control_db

# Run 生命周期与后台 worker 的唯一实现位于 server.lifecycle；
# 路由层只复用，不再维护第二套心跳、报告收敛和工作区清理逻辑。
from server.lifecycle import (
    _claim_worker_lease,
    _clear_worker_active,
    _complete_run_lifecycle,
    _discard_retry_archive,
    _record_background_failure,
    _reinvestigate_task,
    _release_worker_lease,
    _resume_workflow_background,
    _run_workflow_background_v1,
    _settle_task_lifecycles_after_run_failure,
    _start_lifecycle_heartbeat,
)


__all__ = [name for name in dir() if not name.startswith("__")]
