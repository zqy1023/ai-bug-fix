"""工作台运行投影、产物解析与状态计算。"""
from __future__ import annotations

import json
import logging
import os
import re
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from fastapi import BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from core.policy import APPROVAL_RETRY_TARGETS, RetryTarget
from core.policy import legacy_analysis_required
from infrastructure.storage.lifecycle import LifecycleStore
from infrastructure.storage.store import RecordStore
from core.contracts import WORKFLOW_VERSION
from core.policy import AgentPolicy, resolve_policy
from config.settings import settings
from server.context import (
    _ID_PATTERN,
    _RUN_LOCKS,
    _RUN_LOCKS_GUARD,
    SERVICE_NAME,
    SERVICE_VERSION,
    _authorize,
    _config,
    _parse_time,
    _read_json,
    _require_current_workflow,
    _run_lock,
    _safe_id,
    _store,
)
from server.control_store import _control_db
from server.lifecycle import (
    _resume_workflow_background,
    _settle_task_lifecycles_after_run_failure,
)
from server.projections import run_error as project_run_error
from server.projections import run_timestamps, task_stages
from server.view_models import delivery_view, run_actions, task_retry_action

logger = logging.getLogger("workbench.run_service")


class ApprovalDecision(BaseModel):
    # decision：交付审批决定。
    decision: str
    # reason：人工操作说明，用于审计与终态报告。
    reason: str = ""
    # retry_target：驳回后必须明确指定的工作流阶段。
    retry_target: str | None = None

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
    from agents.review.gates.verify_issue.assessment import CONTRACT_VERSION
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "workflow_version": WORKFLOW_VERSION,
        "verification_contract_version": CONTRACT_VERSION,
        "review_contract_version": REVIEW_CONTRACT_VERSION,
        # 该字段只表示「日志行本身是否脱敏」，当前进程日志不脱敏，因此必须如实
        # 报告 False；交付面与异常摘要的脱敏走 redact_for_delivery /
        # public_exception，与这个开关无关，不能据此把它改成 True。
        "log_redaction_enabled": False,
        "runtime_dir_configured": bool(config.runtime_dir),
        "source_repo_configured": config.source_repo is not None,
        from config.nacos import nacos_get
        "api_token_required": bool(nacos_get("WORKBENCH_API_TOKEN", "").strip()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

def _pending_approvals(_: None = Depends(_authorize)) -> dict[str, Any]:
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

def _v1_status(value: str) -> str:
    status = (value or "").lower()
    aliases = {"pending": "queued", "accepted": "queued", "completed": "succeeded", "success": "succeeded"}
    return aliases.get(status, status if status in {
        "queued", "running", "waiting_approval", "succeeded", "partial_failed", "failed", "paused",
        "pause_requested", "cancelled", "cancel_requested",
    } else "failed")

def _v1_run_summary(payload: dict[str, Any]) -> dict[str, Any]:
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

def _v1_detail(run_id: str) -> dict[str, Any]:
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

def _run_payload(
    store: RecordStore, run_id: str, *, substeps_for: str | None = None
) -> dict[str, Any]:
    """构建 Run 聚合投影。

    ``substeps_for`` 指定只加载某个任务的审查内部证据；任务详情只需要当前
    任务，避免为 30+ 个任务读取无关的 review_agent 子步骤产物。
    """
    directory = store.run_dir(run_id)
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail="运行记录不存在")
    from core.contracts import WORKFLOW_VERSION
    if store.load_run(run_id).get("workflow_version") != WORKFLOW_VERSION:
        raise HTTPException(status_code=409, detail="工作流版本不受支持，请新建当前版本执行")
    steps_payload = store.steps_snapshot(run_id)
    run_steps = [
        item.model_dump(mode="json")
        for item in store.list_steps_from_snapshot(run_id, steps_payload)
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
                for item in store.list_steps_from_snapshot(run_id, steps_payload, task_id)
            ]
            # 审查 Agent 的内部工具（check_changes / verify_issue / run_regression …）
            # 刻意不进 steps.json 主表，但工作台必须能看到它们的执行证据，
            # 否则"变更校验/问题验证/回归验证"三块永远空白。
            if substeps_for is None or task_id == substeps_for:
                task_steps.extend(
                    item.model_dump(mode="json")
                    for item in store.list_review_substeps(run_id, task_id)
                )
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

def _load_model_calls(run_id: str, task_id: str | None = None) -> list[dict[str, Any]]:
    """按行读取 Provider 审计并按任务过滤；损坏行跳过，不伪造数据。"""
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
