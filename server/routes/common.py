"""生产工作流工作台 API。

默认建议只监听 127.0.0.1。远程部署必须设置 WORKBENCH_API_TOKEN 并使用 HTTPS 反向代理。
API 只展示运行时记录，不把源码工作区或任意文件系统路径暴露给浏览器。
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from core.policy import APPROVAL_RETRY_TARGETS, RetryTarget
from infrastructure.llm.config import load_config
from infrastructure.storage.lifecycle import LifecycleStore
from infrastructure.storage.store import RecordStore
from infrastructure.storage.redaction import public_exception
from config.settings import settings
from server.projections import run_error as project_run_error
from server.projections import run_timestamps, task_stages
from server.view_models import delivery_view, run_actions, task_retry_action

from config.nacos import nacos_get
from config.settings import settings

SERVICE_NAME = settings.service_name
SERVICE_VERSION = settings.service_version

_ID_MAX_LENGTH = 160
_ID_PATTERN = re.compile(rf"^[A-Za-z0-9_.-]{{1,{_ID_MAX_LENGTH}}}$")
# Run 级执行锁按 run_id 累积，从不回收会让长驻进程缓慢泄漏内存。
# 用 OrderedDict + 守护锁实现简单 LRU：命中即 move_to_end，超过
# _RUN_LOCKS_LIMIT 时淘汰最旧条目。淘汰不强制回收使用中的锁——
# 正在运行的线程仍持有锁对象引用，淘汰只让后续请求重建锁；
# 并发正确性由 non-blocking acquire 与生命周期落盘共同保证。
_RUN_LOCKS: OrderedDict[str, threading.Lock] = OrderedDict()
_RUN_LOCKS_GUARD = threading.Lock()
_RUN_LOCKS_LIMIT = 4096
_STARTED_RUNS: set[str] = set()
logger = logging.getLogger("server.api")


class ApprovalDecision(BaseModel):
    # decision：交付审批决定。
    decision: str
    # reason：人工操作说明，用于审计与终态报告。
    reason: str = ""
    # retry_target：驳回后必须明确指定的工作流阶段。
    retry_target: str | None = None


def _authorize(authorization: str | None = Header(default=None)) -> None:
    token = nacos_get("WORKBENCH_API_TOKEN", "").strip()
    if not token:
        return
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not supplied or not secrets.compare_digest(supplied, token):
        raise HTTPException(status_code=401, detail="需要有效的工作台 Bearer Token")


def _config():
    import sys
    _mod = sys.modules.get("server.api") or sys.modules.get("server.routes")
    if _mod and "_config" in _mod.__dict__ and _mod.__dict__["_config"] is not _config:
        return _mod.__dict__["_config"]()
    try:
        return load_config()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=public_exception(exc, prefix="运行配置不可用"),
        ) from exc


def _store() -> RecordStore:
    import sys
    _mod = sys.modules.get("server.api") or sys.modules.get("server.routes")
    if _mod and "_store" in _mod.__dict__ and _mod.__dict__["_store"] is not _store:
        return _mod.__dict__["_store"]()
    config = _config()
    return RecordStore(config.runtime_dir)


def _safe_id(value: str) -> str:
    if not _ID_PATTERN.fullmatch(value) or value in {".", ".."}:
        raise HTTPException(status_code=400, detail="无效的 ID")
    return value


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _run_lock(run_id: str) -> threading.Lock:
    """获取（或创建）run 的执行锁；必须在 _RUN_LOCKS_GUARD 内调用。

    命中即 move_to_end 触碰 LRU 顺序；超过 _RUN_LOCKS_LIMIT 时淘汰最旧条目。
    使用中的锁对象仍被运行线程持有，淘汰只让后续请求重建锁，属可接受的取舍。
    """
    lock = _RUN_LOCKS.get(run_id)
    if lock is None:
        lock = threading.Lock()
        _RUN_LOCKS[run_id] = lock
    else:
        _RUN_LOCKS.move_to_end(run_id)
    while len(_RUN_LOCKS) > _RUN_LOCKS_LIMIT:
        _RUN_LOCKS.popitem(last=False)
    return lock


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
    steps_payload = _read_json(store.steps_path(run_id), {})
    if not isinstance(steps_payload, dict):
        steps_payload = {}
    run_steps = [        item.model_dump(mode="json")
        for item in store.list_steps(run_id)
    ]
    task_slots = steps_payload.get("tasks", [])
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
                for item in store.list_steps(run_id, task_id)
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
            task_prob = dict(_task_problem(slot))
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

    if not worker_active and (run_failure or run_report) and lifecycle_status in {"", "failed", "partial_failed", "succeeded", "cancelled"}:
        if any(task["status"] in {"RUNNING", "RETRYING", "PENDING"} for task in tasks):
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
            results = [str(task["status"]).upper() for task in tasks]

    if lifecycle_status in {"queued", "running", "retrying", "pause_requested", "cancel_requested"} and not worker_active:
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
        "dispatched_tasks": len(tasks),
        "run_error": project_run_error(run_failure if isinstance(run_failure, dict) else {}, run_lifecycle),
        **run_timestamps(run_lifecycle, run_report, run_failure, created_at=created, terminal=terminal),
        "created_at": created,
        "updated_at": updated,
        "steps": run_steps,
        "tasks": tasks,
        "summary": {
            "tasks_total": len(tasks),
            "tasks_success": sum(1 for result in results if result in {"SUCCESS", "SUCCEEDED"}),
            "tasks_failed": sum(1 for result in results if result in {"FAILED", "FAIL"}),
            "tasks_manual": sum(1 for result in results if result in {"MANUAL", "REJECTED"}),
            "tasks_blocked": sum(1 for result in results if result == "BLOCKED"),
            "tasks_waiting_approval": pending,
            "tasks_inactive": sum(1 for result in results if result in {"CANCELLED", "SKIPPED", "COMPLETED"}),
            "steps_total": len(run_steps) + sum(len(task["steps"]) for task in tasks),
        },
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
    try:
        fingerprint = str(approval_input.get("approval_fingerprint") or "")
        resume_payload = {"decision": decision, "approval_fingerprint": fingerprint, "task_id": task_id}
        if decision == "REJECT":
            resume_payload.update({
                "reason": str(request.reason or "").strip()[:1000],
                "retry_target": str(request.retry_target),
            })
        # Transfer lock ownership only after the background task is enqueued.
        background.add_task(_resume_workflow_background, run_id, lock, resume_payload)
    except BaseException:
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
    review_mode: Literal["agent", "human"] = settings.default_review_mode
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


def _control_db():
    """Durable idempotency, stop-request and audit metadata, separate from workflow artifacts."""
    import sqlite3
    config = _config()
    runtime_dir = config.runtime_dir
    runtime_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        str(runtime_dir / "workbench-control.sqlite3"),
        timeout=config.workbench_sqlite_timeout_seconds,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(f"PRAGMA busy_timeout={config.workbench_sqlite_busy_timeout_ms}")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            key TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, run_id TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS run_control (
            run_id TEXT PRIMARY KEY, pause_requested INTEGER NOT NULL DEFAULT 0,
            cancel_requested INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS control_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, task_id TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL, actor TEXT NOT NULL DEFAULT 'unknown', reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
    """)
    connection.commit()
    return connection


def _audit_control(run_id: str, task_id: str, action: str, actor: str, reason: str = "") -> None:
    connection = _control_db()
    try:
        connection.execute(
            "INSERT INTO control_audit(run_id,task_id,action,actor,reason,created_at) VALUES(?,?,?,?,?,?)",
            (run_id, task_id, action, actor[:160], reason[:1000], datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
    finally:
        connection.close()


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
        "failed_tasks": int(summary.get("tasks_failed") or 0),
        "blocked_tasks": int(summary.get("tasks_blocked") or 0),
        "manual_tasks": int(summary.get("tasks_manual") or 0),
        "waiting_tasks": int(summary.get("tasks_waiting_approval") or 0),
        "inactive_tasks": int(summary.get("tasks_inactive") or 0),
        "operator": payload.get("operator") or "系统", "created_at": created, "updated_at": payload.get("updated_at") or created,
    }


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


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
        result = str(task.get("status") or "PENDING")
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
        from workflow.engine.wiring import build_graph
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
    from core.contracts import StepError

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
        from infrastructure.storage.run_control import cancellation_requested

        try:
            cancelled = cancellation_requested(_store().root, run_id)
        except Exception:
            cancelled = False
        if cancelled:
            logger.info(
                "后台异常发生在停止请求之后，按 CANCELLED 收敛 run_id=%s error=%s",
                run_id, type(error).__name__,
            )
            from workflow.engine.entrypoint import _write_run_report

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
    from workflow.engine.entrypoint import run_workflow
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
        from infrastructure.llm.runtime import close_sessions
        close_sessions(run_id)
        _clear_worker_active(run_id)
        try:
            from infrastructure.workspace.workspace_gc import (
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
        from infrastructure.llm.config import load_config, workflow_recursion_limit
        from workflow.engine.wiring import build_graph, build_services

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

            from workflow.engine.wiring import resume_task_approval

            task_id = str(resume_payload.get("task_id") or "")
            detached = bool(task_id) and resume_task_approval(
                store, run_id, task_id, resume_payload, runtime_config,
                build_services(config=runtime_config, source_repo=source_repo),
            )
            # 嵌套任务子图的 interrupt 在部分 LangGraph 版本中不一定完整暴露在父快照的
            # task.interrupts 上。审批接口已校验当前任务处于待决状态且持有 run 锁，因此
            # 必须直接把决定交给 checkpoint；绝不能因快照未显示 interrupt 而重新 invoke(None)。
            if detached:
                from workflow.engine.routes import assert_workflow_version
                assert_workflow_version(_store().load_run(run_id))
                final_state = graph.invoke(None, config=config) if snapshot.next else dict(snapshot.values)
            else:
                from workflow.engine.routes import assert_workflow_version
                assert_workflow_version(_store().load_run(run_id))
                final_state = graph.invoke(Command(resume=resume_payload), config=config)
        elif has_interrupt:
            from langgraph.types import Command

            from workflow.engine.routes import assert_workflow_version
            assert_workflow_version(_store().load_run(run_id))
            final_state = graph.invoke(Command(resume={"control": "resume"}), config=config)
        else:
            from workflow.engine.routes import assert_workflow_version
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
        from workflow.engine.entrypoint import _write_run_report_if_terminal
        _write_run_report_if_terminal(store, run_id, graph, final_state=final_state)
        _complete_run_lifecycle(run_id, store)
        if (store.run_dir(run_id) / "reports" / "final.json").is_file():
            _discard_retry_archive(store, run_id)
    except Exception as exc:
        logger.exception("恢复工作流失败 run_id=%s", run_id)
        phase = "approval_recovery" if resume_payload is not None else "workflow_resume"
        _record_background_failure(run_id, exc, phase=phase)
    finally:
        from infrastructure.llm.runtime import close_sessions
        close_sessions(run_id)
        if heartbeat_stop is not None:
            heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=1)
        _clear_worker_active(run_id)
        try:
            from infrastructure.workspace.workspace_gc import (
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
    from workflow.stages.claim_slot.node import renew_claim

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


















class V1StepRunRequest(BaseModel):
    # confirm_write：写步骤必须显式确认，避免误触外部写入。
    confirm_write: bool = False
    # continue_after：默认不推进工作流；需要继续时必须由调用方显式要求。
    continue_after: bool = False


def _load_model_calls(run_id: str, task_id: str | None = None) -> list[dict[str, Any]]:
    import sys
    _mod = sys.modules.get("server.api") or sys.modules.get("server.routes")
    if _mod and "_load_model_calls" in _mod.__dict__ and _mod.__dict__["_load_model_calls"] is not _load_model_calls:
        return _mod.__dict__["_load_model_calls"](run_id, task_id)
    import sys
    api_mod = sys.modules.get("server.api")
    if api_mod and "_load_model_calls" in api_mod.__dict__ and api_mod.__dict__["_load_model_calls"] is not _load_model_calls:
        return api_mod.__dict__["_load_model_calls"](run_id, task_id)
    """从运行目录读取 Provider 真实落盘记录，消除内存伪数据。"""
    from infrastructure.storage.database import model_calls
    return model_calls(_store().run_dir(run_id), task_id)

def _formal_step_payload(store: RecordStore, run_id: str, task_id: str, record: dict) -> dict:
    """任务、步骤和审批共享正式输出，禁止使用截断审计摘要推断候选身份。"""
    from core.contracts import Handoff

    name = str(record.get("name") or "")
    root = store.task_dir(run_id, task_id) if task_id else store.run_dir(run_id)
    if name and (root / f"workflow/outputs/{name}.json").is_file():
        try:
            raw = store.read_artifact(run_id, f"workflow/outputs/{name}.json", task_id or None)
            if isinstance(raw, dict):
                try:
                    formal = Handoff.model_validate(raw)
                    return {**record, "output": formal.outcome.output,
                            "execution": formal.outcome.execution.model_dump(mode="json"),
                            "assessment": formal.outcome.assessment.model_dump(mode="json")}
                except Exception:
                    outcome = raw.get("outcome") if isinstance(raw.get("outcome"), dict) else {}
                    exec_info = outcome.get("execution") if isinstance(outcome.get("execution"), dict) else {}
                    assess_info = outcome.get("assessment") if isinstance(outcome.get("assessment"), dict) else {}
                    output_info = outcome.get("output") if isinstance(outcome.get("output"), dict) else record.get("output") or {}
                    return {
                        **record,
                        "output": output_info,
                        "execution": exec_info,
                        "assessment": assess_info,
                    }
        except Exception:
            logger.warning("读取步骤正式交接产物失败 name=%s run_id=%s task_id=%s", name, run_id, task_id, exc_info=True)
    return record


def _step_context(record: Any) -> dict[str, Any]:
    """步骤上下文白名单投影；不返回工作区绝对路径与完整输入输出。"""
    output = record.output if isinstance(record.output, dict) else {}
    plan = output.get("change_plan") if isinstance(output.get("change_plan"), dict) else {}
    files = output.get("changed_files") if isinstance(output.get("changed_files"), list) else []
    return {
        "attempt": record.attempt,
        "status": record.status,
        "result": record.result,
        "changed_files": [str(item) for item in files if str(item)][:50],
        "change_plan_scope": str(plan.get("scope") or ""),
        "delivery_status": str(output.get("delivery_status") or ""),
        "counts": {str(key): len(value) for key, value in output.items() if isinstance(value, list)},
        "flags": {
            key: bool(output[key])
            for key in ("legacy_only", "scope_escalation_required", "approval_pending")
            if key in output
        },
    }




def _reinvestigate_task(run_id: str, task_id: str, background: BackgroundTasks) -> dict[str, Any]:
    """为人工任务建立独立调查 Run，保留原报告、分支、回执和审批检查点。"""
    import shutil

    from core.contracts import StepRecord
    from infrastructure.llm.config import load_config
    from workflow.engine.routes import IDENTIFY
    from workflow.engine.wiring import build_graph, build_services

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










def _require_current_workflow(run_id: str) -> None:
    from workflow.engine.routes import assert_workflow_version
    try:
        assert_workflow_version(_store().load_run(run_id))
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

__all__ = [name for name in dir() if not name.startswith("__")]
