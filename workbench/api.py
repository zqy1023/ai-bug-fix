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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.infrastructure.config import load_config
from app.infrastructure.records.store import RecordStore
from app.infrastructure.records.lifecycle import LifecycleStore
from app.infrastructure.redaction import public_exception
from app.domain.approval import APPROVAL_RETRY_TARGETS, RetryTarget
from config.settings import settings

router = APIRouter()
SERVICE_NAME = "ai-repair-workbench"
SERVICE_VERSION = "1.0.0"

_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
_RUN_LOCKS: dict[str, threading.Lock] = {}
_RUN_LOCKS_GUARD = threading.Lock()
_STARTED_RUNS: set[str] = set()
logger = logging.getLogger("workbench.api")


class StartRunRequest(BaseModel):
    # run_id：可选的调用方指定执行标识；缺省由服务端生成。
    run_id: str | None = Field(default=None, max_length=160)


class ApprovalRequest(BaseModel):
    # decision：交付审批决定。
    decision: str
    # reason：人工操作说明，用于审计与终态报告。
    reason: str = ""
    # retry_target：驳回后必须明确指定的工作流阶段。
    retry_target: str | None = None


def _authorize(authorization: str | None = Header(default=None)) -> None:
    token = os.getenv("WORKBENCH_API_TOKEN", "").strip()
    if not token:
        return
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not supplied or not secrets.compare_digest(supplied, token):
        raise HTTPException(status_code=401, detail="需要有效的工作台 Bearer Token")


def _config():
    try:
        return load_config()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=public_exception(exc, prefix="运行配置不可用"),
        ) from exc


def _store() -> RecordStore:
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


def _run_payload(store: RecordStore, run_id: str) -> dict[str, Any]:
    directory = store.run_dir(run_id)
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail="运行记录不存在")
    steps_payload = _read_json(store.steps_path(run_id), {})
    if not isinstance(steps_payload, dict):
        steps_payload = {}
    run_steps = [        item.model_dump(mode="json")
        for item in store.list_steps(run_id)
    ]
    task_slots = steps_payload.get("tasks", [])
    lifecycle_store = LifecycleStore(store.root)
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
            inferred_status = derived_step_status
            lifecycle_status = str(task_lifecycle.get("status") or "").lower()
            task_status_labels = {
                "pending": "PENDING", "queued": "PENDING", "running": "RUNNING",
                "retrying": "RETRYING", "waiting_approval": "WAITING_APPROVAL",
                "paused": "PAUSED", "succeeded": "SUCCESS", "partial_failed": "PARTIAL_FAILED",
                "failed": "FAILED", "cancelled": "CANCELLED", "manual": "MANUAL",
            }
            fallback_report = _report_from_steps(
                task_id=task_id, carrier=carrier, biztype=biztype,
                steps=task_steps, status=inferred_status,
            )
            if not isinstance(report, dict) or not report:
                report = fallback_report
            elif not report.get("terminal_reason") or not report.get("terminal_step") or not report.get("next_action"):
                # 旧 final.json 可能只有 MANUAL/FAILED，按步骤记录补全报告；已存在字段不覆盖。
                report = {
                    **report,
                    "terminal_step": report.get("terminal_step") or fallback_report.get("terminal_step"),
                    "terminal_reason": report.get("terminal_reason") or fallback_report.get("terminal_reason"),
                    "next_action": report.get("next_action") or fallback_report.get("next_action"),
                    "legacy_report": True,
                }
            approval_pending = _approval_pending(approval) or lifecycle_status == "waiting_approval"
            active_derived_statuses = {"RUNNING", "PENDING", "WAITING_APPROVAL", "PAUSED"}
            task_status = (
                task_status_labels[lifecycle_status]
                if lifecycle_status in task_status_labels
                else derived_step_status
                if derived_step_status in active_derived_statuses
                else str(report.get("result") or derived_step_status).upper()
            )
            tasks.append({
                "task_id": task_id, "carrier": carrier, "biztype": biztype,
                "status": task_status,
                "problem": _task_problem(slot), "steps": task_steps,
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
    worker_active = bool(run_lock and run_lock.locked())
    heartbeat_at = _parse_time(run_lifecycle.get("heartbeat_at"))
    heartbeat_fresh = bool(heartbeat_at and (datetime.now(timezone.utc) - heartbeat_at).total_seconds() <= 30)
    worker_active = worker_active or (
        lifecycle_status in {"queued", "running", "retrying", "cancel_requested", "pause_requested"}
        and heartbeat_fresh
    )

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

    lifecycle_to_run = {
        "queued": "queued", "running": "running", "retrying": "running",
        "pause_requested": "pause_requested", "paused": "paused",
        "waiting_approval": "waiting_approval", "cancel_requested": "cancel_requested",
        "succeeded": "succeeded", "partial_failed": "partial_failed",
        "failed": "failed", "cancelled": "cancelled", "manual": "partial_failed",
    }
    # A terminal lifecycle must not be shown as running just because the worker
    # still holds its lock while flushing failure artifacts and releasing resources.
    if lifecycle_status in {"succeeded", "partial_failed", "failed", "cancelled", "manual"}:
        status = lifecycle_to_run[lifecycle_status]
    elif worker_active:
        status = "running"
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
        "run_error": run_failure if isinstance(run_failure, dict) else (
            {
                "status": "FAILED", "error_type": run_lifecycle.get("error_type"),
                "message": run_lifecycle.get("error_message") or run_lifecycle.get("terminal_reason"),
                "terminal_step": run_lifecycle.get("phase"),
                "terminal_reason": run_lifecycle.get("terminal_reason"),
                "next_action": run_lifecycle.get("next_action"),
            } if lifecycle_status == "failed" else {}
        ),
        "created_at": created,
        "updated_at": updated,
        "steps": run_steps,
        "tasks": tasks,
        "summary": {
            "tasks_total": len(tasks),
            "tasks_success": sum(1 for result in results if result in {"SUCCESS", "SUCCEEDED"}),
            "tasks_failed": sum(1 for result in results if result in {"FAILED", "FAIL"}),
            "tasks_manual": sum(1 for result in results if result in {"MANUAL", "REJECTED"}),
            "tasks_waiting_approval": pending,
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


def _report_from_steps(
    *, task_id: str, carrier: str, biztype: str,
    steps: list[dict[str, Any]], status: str,
) -> dict[str, Any]:
    """兼容旧运行：从已有持久化事实恢复报告，绝不把人工转派伪装成普通完成。"""
    ordered = sorted(steps, key=lambda item: str(item.get("finished_at") or item.get("started_at") or ""))
    final = next((item for item in reversed(ordered) if item.get("name") == "finalize_execution"), None)
    final_output = final.get("output") if isinstance(final, dict) and isinstance(final.get("output"), dict) else {}
    result = str(final_output.get("delivery_status") or status or "INCOMPLETE").upper()
    terminal = ordered[-1] if ordered else {}
    terminal_name = str(final_output.get("terminal_step") or terminal.get("name") or "workflow")
    reason = str(final_output.get("terminal_reason") or "").strip()
    next_action = str(final_output.get("next_action") or "").strip()

    if not reason:
        evidence = next((item for item in reversed(ordered) if item.get("name") == "prepare_evidence"), None)
        evidence_output = evidence.get("output") if isinstance(evidence, dict) and isinstance(evidence.get("output"), dict) else {}
        # legacy_only 只表示“不能自动修复”，后续仍会进入 locate 人工分析；
        # 只有证据准备确实是最后一步时才能把它当成任务终态。
        if evidence_output.get("legacy_only") and terminal.get("name") == "prepare_evidence":
            terminal_name = "prepare_evidence"
            reason = str(evidence_output.get("legacy_reason") or evidence.get("summary") or
                         "证据准备确认该运输商只有旧框架实现，不能进入自动修复。")
            result = "MANUAL"
            next_action = "人工核对实际采集入口和调用链，确定迁移或兼容方案。"

    if not reason and result in {"MANUAL", "INSUFFICIENT_EVIDENCE"}:
        manual = next((item for item in reversed(ordered) if
                       str(item.get("result") or "").upper() in {"MANUAL", "INSUFFICIENT_EVIDENCE"}
                       or str((item.get("output") or {}).get("status") or "").upper() in {"MANUAL", "INSUFFICIENT_EVIDENCE"}), None)
        if manual:
            output = manual.get("output") if isinstance(manual.get("output"), dict) else {}
            terminal_name = str(manual.get("name") or terminal_name)
            gaps = output.get("evidence_gaps")
            reason = str(output.get("legacy_reason") or output.get("reason") or output.get("summary") or
                         manual.get("summary") or (("证据缺口：" + "；".join(map(str, gaps[:8]))) if isinstance(gaps, list) and gaps else "")).strip()
            generic_locate = {
                "定位输出不满足证据或路径契约，停止并转人工",
                "定位输出不满足证据或路径契约，停止并转人工。",
            }
            if str(manual.get("name") or "") == "locate_agent" and reason in generic_locate and isinstance(gaps, list):
                detail = next((str(item).strip() for item in gaps if str(item).strip()), "")
                if detail:
                    reason = "定位输出契约校验失败：" + detail[:1500]
        reason = reason or "安全门禁要求人工介入，但历史步骤没有保存明确原因。"
        next_action = next_action or "补齐缺失证据并由人工确认处理范围；证据充分前不要自动修改代码。"

    if not reason and result in {"FAILED", "FAIL", "REJECTED"}:
        failure = next((item for item in reversed(ordered) if item.get("status") == "failed"
                        or str(item.get("result") or "").upper() in {"FAIL", "FAILED", "REJECTED"}), terminal)
        error = failure.get("error") if isinstance(failure, dict) and isinstance(failure.get("error"), dict) else {}
        terminal_name = str((failure or {}).get("name") or terminal_name)
        reason = str(error.get("message") or (failure or {}).get("summary") or
                     "历史任务未通过自动修复门禁，但没有保存更具体的失败原因。")
        next_action = next_action or "检查失败节点的错误详情和产物，修复原因后再重试或转人工。"

    if not reason:
        if result in {"RUNNING", "PENDING", "WAITING_APPROVAL"}:
            reason = "任务仍在执行或等待人工决策，尚未形成终态。"
            next_action = next_action or "继续观察任务步骤；若等待审批，请进入审批页面处理。"
        elif result in {"SUCCESS", "SUCCEEDED", "COMPLETED"}:
            reason = "历史任务已结束，但没有保存终态报告；此结论仅基于已有步骤记录推导。"
            next_action = next_action or "查看交付记录并按团队流程完成人工评审和合并。"
        else:
            reason = "历史任务缺少正式终态报告；现有步骤记录未提供更具体的结束原因。"
            next_action = next_action or "检查最后一个已执行步骤的输入、输出和错误后确定处置方案。"

    return {
        "report_version": 0, "report_kind": "task_final",
        "legacy_report": result not in {"RUNNING", "PENDING", "WAITING_APPROVAL"},
        "task_id": task_id, "carrier": carrier, "biztype": biztype,
        "result": result, "status": result, "terminal_status": result,
        "terminal_step": terminal_name, "terminal_reason": reason,
        "next_action": next_action or "检查终态步骤及关联产物，确认后续处置动作。",
        "finished_at": final_output.get("finished_at") or terminal.get("finished_at") or "",
        "elapsed_ms": round(sum(float(item.get("duration_ms") or 0) for item in ordered), 2),
        "completed_steps": [str(item.get("name")) for item in ordered if item.get("status") in {"succeeded", "skipped"}],
    }


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
    if step.get("status") == "running":
        return True
    output = step.get("output")
    return isinstance(output, dict) and output.get("decision") == "PENDING"


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


@router.get("/api/health")
def health(_: None = Depends(_authorize)) -> dict[str, Any]:
    config = _config()
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "runtime_dir_configured": bool(config.runtime_dir),
        "source_repo_configured": config.source_repo is not None,
        "api_token_required": bool(os.getenv("WORKBENCH_API_TOKEN", "").strip()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/runs")
def list_runs(
    limit: int = Query(default=50, ge=1, le=200),
    _: None = Depends(_authorize),
) -> dict[str, Any]:
    store = _store()
    items = []
    for run_id in _all_run_ids(store)[:limit]:
        try:
            payload = _run_payload(store, run_id)
            items.append({
                **{key: payload[key] for key in ("run_id", "status", "created_at", "updated_at", "summary")},
                "tasks": [
                    {
                        "task_id": task["task_id"],
                        "carrier": task["carrier"],
                        "biztype": task["biztype"],
                        "status": task["status"],
                        "problem": task["problem"],                        "steps_count": len(task["steps"]),
                        "approval_pending": task["approval_pending"],
                    }
                    for task in payload["tasks"]
                ],
            })
        except (OSError, ValueError):
            continue
    return {"items": items, "total": len(items)}


@router.get("/api/runs/{run_id}")
def get_run(run_id: str, _: None = Depends(_authorize)) -> dict[str, Any]:
    return _run_payload(_store(), _safe_id(run_id))


@router.get("/api/approvals")
def list_approvals(_: None = Depends(_authorize)) -> dict[str, Any]:
    store = _store()
    approvals = []
    for run_id in _all_run_ids(store):
        try:
            run = _run_payload(store, run_id)
        except (HTTPException, OSError, ValueError):
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


@router.post("/api/runs", status_code=202)
def start_run(
    request: StartRunRequest,
    background: BackgroundTasks,
    _: None = Depends(_authorize),
) -> dict[str, Any]:
    config = _config()
    if config.source_repo is None:
        raise HTTPException(status_code=409, detail="未配置 AI_REPAIR_SOURCE_URL 或 AI_REPAIR_SOURCE_REPO，不能启动生产工作流")
    run_id = _safe_id(request.run_id or uuid.uuid4().hex)
    with _RUN_LOCKS_GUARD:
        if run_id in _STARTED_RUNS or _store().run_dir(run_id).joinpath("run.json").exists():
            raise HTTPException(status_code=409, detail="该 run_id 已存在或已提交；请使用新的 run_id，禁止重复启动覆盖")
        lock = _RUN_LOCKS.setdefault(run_id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="该 run 正在启动或恢复")
        _STARTED_RUNS.add(run_id)
    try:
        # 旧 /api 入口复用 v1 的预创建、后台失败报告和生命周期锁；只保留旧响应形状。
        from app.entrypoint import prepare_run
        prepare_run(run_id, overrides={}, queued=True)
    except Exception as exc:
        lock.release()
        with _RUN_LOCKS_GUARD:
            _STARTED_RUNS.discard(run_id)
        raise HTTPException(
            status_code=503,
            detail=public_exception(exc, prefix="初始化执行记录失败，工作流未启动"),
        ) from exc
    background.add_task(_run_workflow_background_v1, run_id, {}, lock)
    return {"run_id": run_id, "status": "accepted", "message": "已提交后台执行；请在运行详情中观察节点进度"}


@router.get("/api/runs/{run_id}/artifacts")
def list_artifacts(
    run_id: str,
    task_id: str = "",
    _: None = Depends(_authorize),
) -> dict[str, Any]:
    store = _store()
    run = _run_payload(store, _safe_id(run_id))
    selected = next((task for task in run["tasks"] if task["task_id"] == task_id), None) if task_id else None
    records = selected["steps"] if selected else run["steps"]
    artifacts = []
    for step in records:
        for ref in step.get("artifacts") or []:
            artifacts.append({
                "task_id": task_id or "",
                "step": step.get("name", ""),
                "name": ref.get("name", ""),
                "path": ref.get("path", ""),
                "size_bytes": None,
            })
    return {"items": artifacts, "total": len(artifacts)}


@router.get("/api/runs/{run_id}/artifacts/content")
def get_artifact_content(
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


@router.post("/api/runs/{run_id}/tasks/{task_id}/approval")
def decide_approval(
    run_id: str,
    task_id: str,
    request: ApprovalRequest,
    background: BackgroundTasks,
    _: None = Depends(_authorize),
) -> dict[str, Any]:
    decision = request.decision.upper()
    if decision not in {"APPROVE", "REJECT"}:
        raise HTTPException(status_code=422, detail="变更审批 decision 只能是 APPROVE 或 REJECT")
    if decision == "REJECT" and not request.retry_target:
        raise HTTPException(status_code=422, detail="驳回必须明确指定回退节点")
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
        lock = _RUN_LOCKS.setdefault(run_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该 run 正在执行恢复操作")
    fingerprint = str(approval_input.get("approval_fingerprint") or "")
    resume_payload = {"decision": decision, "approval_fingerprint": fingerprint}
    if decision == "REJECT":
        retry_target = str(request.retry_target)
        if retry_target not in APPROVAL_RETRY_TARGETS:
            raise HTTPException(status_code=422, detail="无效的驳回回退节点")
        resume_payload.update({
            "reason": str(request.reason or "").strip()[:1000],
            "retry_target": retry_target,
        })
    # 审批 API 只负责校验决定并排入恢复任务。恢复过程可能继续执行多个 carrier，
    # 不应占用 HTTP 请求；同一个 run 的锁交给后台恢复器持有，直至整张父图完成。
    background.add_task(
        _resume_workflow_background,
        run_id,
        lock,
        resume_payload,
    )
    return {"run_id": run_id, "task_id": task_id, "decision": decision, "status": "resume_requested"}

# Versioned control-plane contract consumed by frontend/src/lib/api.ts.
# Legacy /api routes remain available for existing local tooling.
from hashlib import sha256
from typing import Literal


class V1StartRunRequest(BaseModel):
    # log_days：本轮向 ES 回溯的天数。
    log_days: int = Field(default=1, ge=1, le=settings.workbench_max_log_days)
    # log_limit：本轮最多读取的日志数；0 表示采用服务端配置。
    log_limit: int = Field(default=0, ge=0, le=settings.workbench_max_log_limit)
    # review_mode：agent 表示 AI 审查，human 表示强制人工审查。
    review_mode: Literal["agent", "human"] = settings.default_review_mode
    # max_attempts：单任务修复尝试总次数，包含首次执行。
    max_attempts: int = Field(
        default=settings.default_max_attempts,
        ge=1,
        le=settings.workbench_max_attempts,
    )


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
        str(runtime_dir / settings.workbench_control_db_name),
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
    # 旧版控制库原位升级；保留已有运行记录与取消请求。
    columns = {row[1] for row in connection.execute("PRAGMA table_info(run_control)")}
    if "pause_requested" not in columns:
        connection.execute("ALTER TABLE run_control ADD COLUMN pause_requested INTEGER NOT NULL DEFAULT 0")
        connection.commit()
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
    summary = payload.get("summary") or {}
    created = payload.get("created_at") or payload.get("updated_at")
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
            status = "paused" if paused_step else "pause_requested"
    except Exception:
        logger.debug("读取执行控制状态失败 run_id=%s", run_id, exc_info=True)
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
        "manual_tasks": int(summary.get("tasks_manual") or 0),
        "waiting_tasks": int(summary.get("tasks_waiting_approval") or 0),
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
    try:
        from app.workflow.wiring import build_graph
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
        logger.debug("无法读取执行 checkpoint run_id=%s", run_id, exc_info=True)
    status = _v1_status(str(summary.get("status") or payload.get("status") or ""))
    connection = _control_db()
    try:
        row = connection.execute(
            "SELECT pause_requested,cancel_requested FROM run_control WHERE run_id=?", (run_id,)
        ).fetchone()
        terminal_run_statuses = {"succeeded", "partial_failed", "failed", "cancelled"}
        # Stale control flags must not overwrite an already settled Run result.
        if row and row["cancel_requested"] and status not in terminal_run_statuses:
            status = "cancelled" if not next_nodes and not summary.get("worker_active") else "cancel_requested"
        elif row and row["pause_requested"] and status not in terminal_run_statuses:
            has_waiting_step = any(step.get("status") == "waiting" for step in steps)
            has_waiting_step = has_waiting_step or any(
                step.get("status") == "waiting"
                for task in (payload.get("tasks") or []) for step in (task.get("steps") or [])
            )
            status = "paused" if has_waiting_step else "pause_requested"
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
    return {
        **summary, "status": status, "terminal": terminal,
        "config": {
            "log_days": run_meta.get("log_days", 1), "log_limit": run_meta.get("log_limit", 0),
            "review_mode": run_meta.get("review_mode", settings.default_review_mode), "max_attempts": run_meta.get("max_attempts", settings.default_max_attempts),
        },
        "steps": steps, "tasks": tasks, "artifacts": artifacts,
        "run_report": run_report if isinstance(run_report, dict) else {},
        "run_error": payload.get("run_error") or {},
        "checkpoint": {
            "resumable": resumable, "next_nodes": next_nodes,
            "checkpoint_id": checkpoint_id, "created_at": checkpoint_created_at,
            "node": checkpoint_node, "parent_checkpoint_id": checkpoint_parent_id,
            "has_interrupt": checkpoint_has_interrupt,
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


@router.get("/api/v1/health")
def v1_health(_: None = Depends(_authorize)) -> dict[str, Any]:
    return health()


@router.get("/api/v1/dashboard/summary")
def v1_dashboard(_: None = Depends(_authorize)) -> dict[str, Any]:
    store = _store()
    summaries = []
    for run_id in _all_run_ids(store):
        try:
            summaries.append(_v1_run_summary(_run_payload(store, run_id)))
        except (OSError, ValueError, HTTPException):
            continue
    now = datetime.now(timezone.utc)
    day_start = now - timedelta(hours=settings.dashboard_recent_hours)
    week_start = now.date() - timedelta(days=settings.dashboard_trend_days - 1)
    recent = [item for item in summaries if _parse_time(item.get("started_at")) and _parse_time(item.get("started_at")) >= day_start]
    daily = {}
    for offset in range(7):
        day = (week_start + timedelta(days=offset)).isoformat()
        daily[day] = {"date": day, "succeeded": 0, "failed": 0}
    for item in summaries:
        created = _parse_time(item.get("started_at"))
        if created and created.date().isoformat() in daily:
            daily[created.date().isoformat()]["succeeded"] += item["succeeded_tasks"]
            daily[created.date().isoformat()]["failed"] += item["failed_tasks"]
    recent_week = [item for item in summaries if (created := _parse_time(item.get("started_at"))) and created.date() >= week_start]
    total = sum(item["succeeded_tasks"] + item["failed_tasks"] for item in recent_week)
    successes = sum(item["succeeded_tasks"] for item in recent_week)
    return {
        "active_runs": sum(item["status"] in {"queued", "running", "pause_requested", "cancel_requested"} for item in summaries),
        "waiting_approvals": sum(item["waiting_tasks"] for item in summaries),
        "succeeded_24h": sum(item["succeeded_tasks"] for item in recent),
        "failed_24h": sum(item["failed_tasks"] for item in recent),
        "success_rate_7d": successes / total if total else 0.0,
        "recent_runs": summaries[:8], "daily_trend": list(daily.values()),
    }


@router.get("/api/v1/runs")
def v1_list_runs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(
        default=settings.workbench_page_size,
        ge=1,
        le=settings.workbench_max_page_size,
    ),
    status: str | None = None, keyword: str | None = Query(default=None, max_length=160),
    from_: str | None = Query(default=None, alias="from", max_length=40),
    to: str | None = Query(default=None, max_length=40), _: None = Depends(_authorize),
) -> dict[str, Any]:
    store = _store()
    items = []
    from_time, to_time = _parse_time(from_), _parse_time(to)
    for run_id in _all_run_ids(store):
        try:
            item = _v1_run_summary(_run_payload(store, run_id))
        except (OSError, ValueError, HTTPException):
            continue
        if status and item["status"] != status:
            continue
        if keyword and keyword.casefold() not in run_id.casefold():
            continue
        created = _parse_time(item.get("started_at"))
        if from_time and (not created or created < from_time):
            continue
        if to_time and (not created or created > to_time):
            continue
        items.append(item)
    start = (page - 1) * page_size
    return {"items": items[start:start + page_size], "total": len(items), "page": page, "page_size": page_size}


@router.post("/api/v1/runs", status_code=202)
def v1_start_run(
    request: V1StartRunRequest, background: BackgroundTasks,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=200),
    _: None = Depends(_authorize),
) -> dict[str, Any]:
    config = _config()
    if config.source_repo is None:
        raise HTTPException(status_code=409, detail="未配置 AI_REPAIR_SOURCE_URL 或 AI_REPAIR_SOURCE_REPO，不能启动生产工作流")
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="缺少 Idempotency-Key，请为本次启动生成唯一请求键")

    payload = request.model_dump(mode="json")
    payload_hash = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    run_id = uuid.uuid4().hex
    connection = _control_db()
    run_lock: threading.Lock | None = None
    lock_held = False
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT payload_hash,run_id FROM idempotency_keys WHERE key=?", (idempotency_key,)
        ).fetchone()
        if existing:
            if existing["payload_hash"] != payload_hash:
                raise HTTPException(status_code=409, detail="相同 Idempotency-Key 不允许复用于不同请求参数")
            return {"run_id": existing["run_id"], "status": "accepted", "idempotent_replay": True}

        # 在返回 202 之前持久化 run.json。详情/事件/模型调用接口因此能立即查询到 queued Run，
        # 不再依赖 BackgroundTasks 何时开始执行后才创建目录。
        with _RUN_LOCKS_GUARD:
            if run_id in _STARTED_RUNS or _store().run_dir(run_id).exists():
                raise HTTPException(status_code=409, detail="执行 ID 冲突，请重试")
            run_lock = _RUN_LOCKS.setdefault(run_id, threading.Lock())
            if not run_lock.acquire(blocking=False):
                raise HTTPException(status_code=409, detail="该执行已被另一个工作线程启动")
            lock_held = True
            _STARTED_RUNS.add(run_id)

        try:
            from app.entrypoint import prepare_run
            prepare_run(run_id, overrides=payload, queued=True)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=public_exception(exc, prefix="初始化执行记录失败，工作流未启动"),
            ) from exc

        connection.execute(
            "INSERT INTO idempotency_keys(key,payload_hash,run_id,created_at) VALUES(?,?,?,?)",
            (idempotency_key, payload_hash, run_id, datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        if lock_held and run_lock is not None:
            run_lock.release()
            with _RUN_LOCKS_GUARD:
                _STARTED_RUNS.discard(run_id)
        raise
    finally:
        connection.close()

    try:
        _audit_control(run_id, "", "run.start_requested", "api", json.dumps(payload, ensure_ascii=False))
    except Exception:
        logger.exception("写入启动审计失败 run_id=%s", run_id)
    assert run_lock is not None
    background.add_task(_run_workflow_background_v1, run_id, payload, run_lock)
    return {"run_id": run_id, "status": "accepted", "idempotent_replay": False}


def _start_lifecycle_heartbeat(run_id: str) -> tuple[threading.Event, threading.Thread]:
    """Keep an explicit lease while a worker owns the run lock, including long Agent calls."""
    store = _store()
    lifecycle = LifecycleStore(store.root)
    stop = threading.Event()

    def pulse() -> None:
        while not stop.wait(5):
            try:
                lifecycle.update_run(run_id, heartbeat_at=datetime.now(timezone.utc).isoformat())
            except Exception:
                logger.exception("更新运行心跳失败 run_id=%s", run_id)

    thread = threading.Thread(target=pulse, name=f"run-heartbeat-{run_id}", daemon=True)
    thread.start()
    return stop, thread


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
        elif result in {"MANUAL", "MANUAL_REVIEW_REQUIRED", "INSUFFICIENT_EVIDENCE"}:
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


def _settle_task_lifecycles_after_run_failure(
    run_id: str,
    store: RecordStore,
    *,
    phase: str,
    reason: str,
    next_action: str,
) -> None:
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
        if phase == "approval_recovery" and status == "waiting_approval":
            continue
        if status in {"succeeded", "partial_failed", "failed", "cancelled", "manual"}:
            continue
        lifecycle.update_task(
            run_id, task_id, status="failed",
            terminal_step=str(state.get("current_step") or phase),
            terminal_reason=reason, next_action=next_action,
            error={"code": "RUN_WORKER_FAILED", "message": reason},
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
        payload = {
            "status": "FAILED", "error_type": type(error).__name__,
            "message": reason,
            "terminal_step": phase, "terminal_reason": reason,
            "next_action": next_action,
            "failed_at": now,
        }
        lifecycle = LifecycleStore(_store().root)
        lifecycle.update_run(
            run_id, status="failed", phase=phase, current_step=phase,
            error_type=type(error).__name__, error_message=reason,
            terminal_reason=reason, next_action=next_action,
        )
        _settle_task_lifecycles_after_run_failure(
            run_id, _store(), phase=phase, reason=reason, next_action=next_action,
        )
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        final_path = report_dir / "final.json"
        existing_report = _read_json(final_path, {})
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
            run_lock = _RUN_LOCKS.setdefault(run_id, threading.Lock())
        if not run_lock.acquire(blocking=False):
            logger.warning("跳过重复启动的工作流 run_id=%s", run_id)
            return
    heartbeat_stop: threading.Event | None = None
    heartbeat_thread: threading.Thread | None = None
    try:
        store = _store()
        LifecycleStore(store.root).update_run(
            run_id, status="running", phase="workflow_startup", current_step="", error={},
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
        run_lock.release()
        with _RUN_LOCKS_GUARD:
            _STARTED_RUNS.discard(run_id)


@router.get("/api/v1/runs/{run_id}")
def v1_get_run(run_id: str, _: None = Depends(_authorize)) -> dict[str, Any]:
    return _v1_detail(run_id)


@router.get("/api/v1/runs/{run_id}/checkpoint")
def v1_get_checkpoint(run_id: str, _: None = Depends(_authorize)) -> dict[str, Any]:
    """Return the current checkpoint projection without exposing workflow channel values."""
    run_id = _safe_id(run_id)
    store = _store()
    _v1_detail(run_id)
    try:
        from app.workflow.wiring import build_graph

        graph = build_graph(store=store)
        snapshot = graph.get_state({"configurable": {"thread_id": run_id}})
    except Exception as exc:
        logger.exception("读取 checkpoint 失败 run_id=%s", run_id)
        raise HTTPException(status_code=503, detail="运行 checkpoint 暂时不可读取") from exc
    return {"run_id": run_id, "checkpoint": _checkpoint_projection(snapshot)}


@router.get("/api/v1/runs/{run_id}/checkpoints")
def v1_list_checkpoints(
    run_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    _: None = Depends(_authorize),
) -> dict[str, Any]:
    """List checkpoint metadata for timeline views; state values never leave the server."""
    run_id = _safe_id(run_id)
    store = _store()
    _v1_detail(run_id)
    try:
        from app.workflow.wiring import build_graph

        graph = build_graph(store=store)
        history = graph.get_state_history({"configurable": {"thread_id": run_id}})
        items = []
        for snapshot in history:
            items.append(_checkpoint_projection(snapshot))
            if len(items) >= limit:
                break
    except Exception as exc:
        logger.exception("读取 checkpoint 历史失败 run_id=%s", run_id)
        raise HTTPException(status_code=503, detail="运行 checkpoint 历史暂时不可读取") from exc
    return {"run_id": run_id, "items": items, "total": len(items)}


@router.get("/api/v1/runs/{run_id}/tasks/{task_id}")
def v1_get_task(run_id: str, task_id: str, _: None = Depends(_authorize)) -> dict[str, Any]:
    """只返回一个任务的详情，避免 Run 页面携带并渲染所有任务的步骤与产物。"""
    store = _store()
    run_id, task_id = _safe_id(run_id), _safe_id(task_id)
    payload = _run_payload(store, run_id)
    source = next((item for item in payload.get("tasks", []) if item.get("task_id") == task_id), None)
    if source is None:
        raise HTTPException(status_code=404, detail="该执行中不存在此任务")
    steps = list(source.get("steps") or [])
    report = source.get("report") if isinstance(source.get("report"), dict) else {}
    if not report:
        last = steps[-1] if steps else {}
        report = {
            "result": source.get("status") or "INCOMPLETE",
            "terminal_step": str(last.get("name") or "workflow"),
            "terminal_reason": "历史任务没有持久化终态报告；请检查服务端日志和步骤产物。",
            "next_action": "重新运行该任务以生成完整终态报告；如需排查旧记录，请检查步骤输入、输出和错误。",
            "legacy_report": True,
        }
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
    run_error = _read_json(store.run_dir(run_id) / "reports" / "run_failure.json", {})
    return {
        "run_id": run_id,
        "run_status": _v1_run_summary(payload)["status"],
        "run_error": run_error if isinstance(run_error, dict) else {},
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
            "log_days": run_meta.get("log_days", 1), "log_limit": run_meta.get("log_limit", 0),
            "review_mode": run_meta.get("review_mode", settings.default_review_mode), "max_attempts": run_meta.get("max_attempts", settings.default_max_attempts),
        },
    }


@router.post("/api/v1/runs/{run_id}/pause", status_code=202)
def v1_pause_run(run_id: str, _: None = Depends(_authorize)) -> dict[str, Any]:
    """请求在下一个节点边界暂停；节点完成前返回 pause_requested，而非谎报已暂停。"""
    run_id = _safe_id(run_id)
    detail = _v1_detail(run_id)
    if detail["status"] not in {"running", "queued", "pause_requested"}:
        raise HTTPException(status_code=409, detail="执行当前不处于可暂停状态")
    connection = _control_db()
    try:
        connection.execute(
            "INSERT INTO run_control(run_id,pause_requested,cancel_requested,updated_at) VALUES(?,1,0,?) "
            "ON CONFLICT(run_id) DO UPDATE SET pause_requested=1,updated_at=excluded.updated_at",
            (run_id, datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
    finally:
        connection.close()
    _audit_control(run_id, "", "run.pause_requested", "api")
    return {"run_id": run_id, "status": "pause_requested", "message": "已请求暂停；当前节点完成或到达安全边界后生效"}


@router.post("/api/v1/runs/{run_id}/retry", status_code=202)
def v1_retry_run(run_id: str, background: BackgroundTasks, _: None = Depends(_authorize)) -> dict[str, Any]:
    """重试失败的 Run：优先从 LangGraph checkpoint 恢复，仅允许安全的启动阶段从头重启。"""
    run_id = _safe_id(run_id)
    store = _store()
    detail = _v1_detail(run_id)
    if detail["status"] != "failed":
        raise HTTPException(status_code=409, detail="只有终态失败的 Run 才能重试；活动执行请使用停止或暂停")
    with _RUN_LOCKS_GUARD:
        lock = _RUN_LOCKS.setdefault(run_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该 Run 仍被后台工作线程占用，状态正在收敛")

    try:
        checkpoint = detail.get("checkpoint") if isinstance(detail.get("checkpoint"), dict) else {}
        run_meta = store.load_run(run_id)
        if checkpoint.get("resumable"):
            mode = "resume"
            worker = _resume_workflow_background
        else:
            steps = store.list_steps(run_id)
            raw_steps = _read_json(store.steps_path(run_id), {})
            task_slots = raw_steps.get("tasks", []) if isinstance(raw_steps, dict) else []
            run_step_names = {step.name for step in steps}
            # From-scratch retry is safe only before log discovery/task dispatch.
            # A sync_source_repo-only run may have no checkpoint in older LangGraph versions.
            safe_startup_retry = not task_slots and run_step_names.issubset({"sync_source_repo"})
            if not safe_startup_retry:
                raise HTTPException(
                    status_code=409,
                    detail="当前 Run 没有可恢复的 checkpoint，且已进入业务步骤；请在任务详情中重试失败节点，不能覆盖历史执行。",
                )
            mode = "restart"
            run_json = store.run_dir(run_id) / "run.json"
            run_meta["startup_state"] = "queued"
            temporary = run_json.with_name("run.json.retry.tmp")
            temporary.write_text(json.dumps(run_meta, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(run_json)
            reports = store.run_dir(run_id) / "reports"
            (reports / "run_failure.json").unlink(missing_ok=True)
            (reports / "final.json").unlink(missing_ok=True)
            LifecycleStore(store.root).update_run(
                run_id, status="queued", phase="retry_queued", current_step="",
                started_at=None, finished_at=None, error={},
                error_type="", error_message="", terminal_reason="", next_action="",
            )
            overrides = {
                "log_days": int(run_meta.get("log_days") or 1),
                "log_limit": int(run_meta.get("log_limit") or 0),
                "review_mode": str(run_meta.get("review_mode") or settings.default_review_mode),
                "max_attempts": int(run_meta.get("max_attempts") or settings.default_max_attempts),
            }
            worker = _run_workflow_background_v1

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
        _audit_control(run_id, "", "run.retry_requested", "api", f"mode={mode}")
    except Exception:
        lock.release()
        raise

    if mode == "resume":
        background.add_task(_resume_workflow_background, run_id, lock)
    else:
        background.add_task(worker, run_id, overrides, lock)
    return {
        "run_id": run_id,
        "status": "retry_requested",
        "mode": mode,
        "message": "已提交 Run 重试；请观察当前节点、尝试次数和生命周期状态。",
    }


@router.post("/api/v1/runs/{run_id}/resume", status_code=202)
def v1_resume_run(run_id: str, background: BackgroundTasks, _: None = Depends(_authorize)) -> dict[str, Any]:
    run_id = _safe_id(run_id)
    with _RUN_LOCKS_GUARD:
        lock = _RUN_LOCKS.setdefault(run_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该执行正在进行其他控制操作")

    # 检查点和状态必须在取得执行锁后重新读取，避免并发控制请求在校验后改变状态。
    try:
        detail = _v1_detail(run_id)
        if detail["status"] not in {"running", "queued", "paused", "failed"} or not detail["checkpoint"]["resumable"]:
            raise HTTPException(status_code=409, detail="执行当前没有可恢复的 checkpoint")
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
    except Exception:
        lock.release()
        raise

    try:
        _audit_control(run_id, "", "run.resume_requested", "api")
    except Exception:
        lock.release()
        logger.exception("写入恢复操作审计失败 run_id=%s", run_id)
        raise HTTPException(status_code=503, detail="恢复未提交：写入控制审计失败")
    background.add_task(_resume_workflow_background, run_id, lock)
    return {"run_id": run_id, "status": "resume_requested", "message": "已提交检查点恢复；请观察运行状态变化"}


def _resume_workflow_background(
    run_id: str,
    run_lock: Any = None,
    resume_payload: dict[str, Any] | None = None,
) -> None:
    if run_lock is None:
        with _RUN_LOCKS_GUARD:
            run_lock = _RUN_LOCKS.setdefault(run_id, threading.Lock())
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
            run_id, status="running", phase="workflow_resume", current_step="", error={},
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
            # 嵌套任务子图的 interrupt 在部分 LangGraph 版本中不一定完整暴露在父快照的
            # task.interrupts 上。审批接口已校验当前任务处于待决状态且持有 run 锁，因此
            # 必须直接把决定交给 checkpoint；绝不能因快照未显示 interrupt 而重新 invoke(None)。
            final_state = graph.invoke(Command(resume=resume_payload), config=config)
        elif has_interrupt:
            from langgraph.types import Command
            final_state = graph.invoke(Command(resume=None), config=config)
        else:
            final_state = graph.invoke(None, config=config)
        # 本次恢复已成功推进 checkpoint；清理之前后台异常写入的失败标记，避免 UI 继续把已恢复运行判为 FAILED。
        failure_path = store.run_dir(run_id) / "reports" / "run_failure.json"
        if failure_path.is_file():
            failure_path.unlink()
        stale_run_report = store.run_dir(run_id) / "reports" / "final.json"
        stale_payload = _read_json(stale_run_report, {})
        if (
            isinstance(stale_payload, dict)
            and stale_payload.get("terminal_step") in {"workflow_startup", "approval_recovery", "workflow_resume"}
            and stale_payload.get("result") == "FAILED"
        ):
            stale_run_report.unlink(missing_ok=True)
        from app.entrypoint import _write_run_report_if_terminal
        _write_run_report_if_terminal(store, run_id, graph, final_state=final_state)
        _complete_run_lifecycle(run_id, store)
    except Exception as exc:
        logger.exception("恢复工作流失败 run_id=%s", run_id)
        phase = "approval_recovery" if resume_payload is not None else "workflow_resume"
        _record_background_failure(run_id, exc, phase=phase)
    finally:
        if heartbeat_stop is not None:
            heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=1)
        run_lock.release()


def _renew_open_claim(store: RecordStore, run_id: str, snapshot: Any) -> None:
    """恢复未终态任务前续租本运行的 claim；没有可续租记录时不补领。"""
    task_id = _claimed_task_id(snapshot)
    if not task_id:
        return
    from app.workflow.steps.claim_repair_slot.node import renew_claim

    try:
        renewed = renew_claim(store.root, run_id=run_id, task_id=task_id)
    except Exception:
        logger.exception("恢复前续租修复资格失败 run_id=%s task_id=%s", run_id, task_id)
        return
    if not renewed:
        logger.warning("恢复时没有可续租的修复资格 run_id=%s task_id=%s", run_id, task_id)


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


@router.post("/api/v1/runs/{run_id}/cancel", status_code=202)
def v1_cancel_run(
    run_id: str, background: BackgroundTasks, _: None = Depends(_authorize)
) -> dict[str, Any]:
    run_id = _safe_id(run_id)
    detail = _v1_detail(run_id)
    if detail["status"] not in {"running", "queued", "paused", "pause_requested"}:
        raise HTTPException(status_code=409, detail="执行当前不是可请求停止的状态")
    # 已经挂起的 interrupt 不会自行观察取消标志；恢复检查点让它进入取消收尾。
    paused_lock = None
    if detail["status"] == "paused":
        with _RUN_LOCKS_GUARD:
            paused_lock = _RUN_LOCKS.setdefault(run_id, threading.Lock())
        if not paused_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="执行正在恢复或仍未完全暂停，请稍后重试")
    try:
        connection = _control_db()
    except Exception:
        if paused_lock is not None:
            paused_lock.release()
        raise
    try:
        connection.execute(
            "INSERT INTO run_control(run_id,pause_requested,cancel_requested,updated_at) VALUES(?,0,1,?) "
            "ON CONFLICT(run_id) DO UPDATE SET pause_requested=0,cancel_requested=1,updated_at=excluded.updated_at",
            (run_id, datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
    except Exception:
        if paused_lock is not None:
            paused_lock.release()
        raise
    finally:
        connection.close()
    if paused_lock is not None:
        background.add_task(_resume_workflow_background, run_id, paused_lock)
    _audit_control(run_id, "", "run.cancel_requested", "api")
    return {"run_id": run_id, "status": "cancel_requested", "message": "已记录取消请求；正在执行的节点会在安全边界收尾"}


@router.get("/api/v1/approvals")
def v1_list_approvals(
    status: str = Query(default="pending", pattern="^(pending|approved|rejected|expired)$"),
    _: None = Depends(_authorize),
) -> dict[str, Any]:
    legacy = list_approvals()
    store = _store()
    run_cache: dict[str, dict[str, Any]] = {}
    items = []
    for item in legacy.get("items", []):
        if status != "pending":
            continue
        step = item.get("approval_step") or {}
        approval_input = step.get("input") if isinstance(step.get("input"), dict) else {}
        output = step.get("output") if isinstance(step.get("output"), dict) else {}
        run_id, task_id = str(item.get("run_id") or ""), str(item.get("task_id") or "")
        if run_id not in run_cache:
            try:
                run_cache[run_id] = _run_payload(store, run_id)
            except (HTTPException, OSError, ValueError):
                run_cache[run_id] = {}
        task = next(
            (value for value in run_cache[run_id].get("tasks", []) if str(value.get("task_id") or "") == task_id),
            {},
        )
        steps = task.get("steps") if isinstance(task.get("steps"), list) else []
        repair_step = next((value for value in reversed(steps) if value.get("name") == "repair_agent"), {})
        review_step = next((value for value in reversed(steps) if value.get("name") == "review_agent"), {})
        repair_output = repair_step.get("output") if isinstance(repair_step.get("output"), dict) else {}
        review_output = review_step.get("output") if isinstance(review_step.get("output"), dict) else {}
        repair_plan = repair_output.get("change_plan") if isinstance(repair_output.get("change_plan"), dict) else {}
        review_plan = review_output.get("change_plan") if isinstance(review_output.get("change_plan"), dict) else {}
        # 审查 Agent 新提出的 project 方案覆盖早先 carrier 方案，和工作流实际将审批的状态保持一致。
        if str(review_plan.get("scope") or "").lower() == "project" and isinstance(review_plan.get("edits"), list) and review_plan.get("edits"):
            plan = review_plan
        else:
            plan = repair_plan or review_plan
        raw_edits = plan.get("edits") if isinstance(plan.get("edits"), list) else []
        edits = [
            {
                "path": str(edit.get("path") or ""),
                "action": str(edit.get("action") or "update"),
                "note": str(edit.get("note") or ""),
            }
            for edit in raw_edits if isinstance(edit, dict) and str(edit.get("path") or "").strip()
        ]
        files = plan.get("changed_files") or plan.get("files") or [edit["path"] for edit in edits] or approval_input.get("changed_files") or repair_output.get("changed_files")
        if not isinstance(files, list):
            files = []
        scope = str(plan.get("scope") or approval_input.get("recommended_scope") or "").lower()
        summary = str(
            plan.get("rationale") or repair_output.get("summary") or review_output.get("summary")
            or output.get("summary") or output.get("reason") or "需要人工确认变更方案"
        )
        safe_plan = {
            "scope": "project" if scope == "project" else "carrier",
            "rationale": str(plan.get("rationale") or summary),
            "edits": edits,
        }
        items.append({
            "approval_id": f"{run_id}:{task_id}", "run_id": run_id, "task_id": task_id,
            "carrier": item.get("carrier") or "", "biztype": item.get("biztype") or "",
            "scope": safe_plan["scope"], "status": "pending", "summary": summary,
            "changed_files": list(dict.fromkeys(str(value) for value in files if str(value).strip())),
            "change_plan": safe_plan,
            "created_at": item.get("updated_at") or datetime.now(timezone.utc).isoformat(), "expires_at": None,
        })
    return {"items": items, "total": len(items)}


@router.post("/api/v1/approvals/{approval_id}/decision")
def v1_decide_approval(
    approval_id: str, request: V1ApprovalDecision, background: BackgroundTasks,
    authorization: str | None = Header(default=None),
    _: None = Depends(_authorize),
) -> dict[str, Any]:
    if request.decision == "reject" and not request.retry_target:
        raise HTTPException(status_code=422, detail="驳回必须明确指定回退节点")
    if ":" not in approval_id:
        raise HTTPException(status_code=400, detail="无效的审批 ID")
    run_id, task_id = approval_id.split(":", 1)
    run_id, task_id = _safe_id(run_id), _safe_id(task_id)
    actor = "bearer-token" if authorization else "local-user"
    _audit_control(run_id, task_id, "approval." + request.decision, actor, request.reason)
    return decide_approval(
        run_id,
        task_id,
        ApprovalRequest(
            decision=request.decision.upper(),
            reason=request.reason,
            retry_target=request.retry_target or "repair_agent",
        ),
        background=background,
    )


@router.get("/api/v1/runs/{run_id}/artifacts")
def v1_list_artifacts(run_id: str, _: None = Depends(_authorize)) -> dict[str, Any]:
    detail = _v1_detail(run_id)
    return {"items": detail["artifacts"], "total": len(detail["artifacts"])}


@router.get("/api/v1/runs/{run_id}/artifacts/content")
def v1_artifact_content(
    run_id: str, path: str = Query(..., min_length=1, max_length=500), task_id: str = "",
    _: None = Depends(_authorize),
) -> JSONResponse:
    return get_artifact_content(run_id, path, task_id)


@router.get("/api/v1/runs/{run_id}/model-calls")
def v1_model_calls(
    run_id: str, task_id: str | None = None, _: None = Depends(_authorize),
) -> dict[str, Any]:
    """返回 Provider 层真实请求审计；未记录的旧运行返回空列表，不补造统计。"""
    run_id = _safe_id(run_id)
    task_id = _safe_id(task_id) if task_id else None
    run_dir = _store().run_dir(run_id)
    if not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="运行记录不存在")
    path = run_dir / "model_calls.jsonl"
    items: list[dict[str, Any]] = []
    if path.is_file():
        try:
            # 按行读取调用元数据，避免把整个审计文件一次性复制到内存。
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("忽略损坏的模型调用审计行 run_id=%s", run_id)
                        continue
                    if not isinstance(item, dict):
                        continue
                    if task_id and str(item.get("task_id") or "") != task_id:
                        continue
                    # Run 级默认只展示轻量元数据；只有选中具体任务时才加载该任务事件。
                    item["events"] = []
                    items.append(item)
        except OSError as exc:
            raise HTTPException(status_code=503, detail="模型调用审计暂时不可读取") from exc

    if task_id:
        event_path = run_dir / "tasks" / task_id / "audit" / "agent_events.jsonl"
        if event_path.is_file():
            events_by_call: dict[str, list[dict[str, Any]]] = {}
            try:
                # 每个任务事件文件只顺序读取一次，避免按每条调用重复扫描整份 JSONL。
                with event_path.open("r", encoding="utf-8") as stream:
                    for event_line in stream:
                        if not event_line.strip():
                            continue
                        try:
                            event_record = json.loads(event_line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(event_record, dict):
                            continue
                        call_id = str(event_record.get("model_call_id") or "")
                        event = event_record.get("event")
                        if call_id and isinstance(event, dict):
                            events_by_call.setdefault(call_id, []).append(event)
            except OSError as exc:
                raise HTTPException(status_code=503, detail="Agent 事件轨迹暂时不可读取") from exc
            for item in items:
                call_id = str(item.get("model_call_id") or "")
                item["events"] = events_by_call.get(call_id, [])
                if item["events"]:
                    item["events_path"] = f"tasks/{task_id}/audit/agent_events.jsonl"
    items.sort(key=lambda item: str(item.get("started_at") or ""))
    return {"items": items, "total": len(items), "source": "provider_audit"}


@router.get("/api/v1/runs/{run_id}/events")
def v1_run_events(run_id: str, _: None = Depends(_authorize)) -> StreamingResponse:
    """SSE 状态流：以持久化运行记录为准，客户端重连后重新获取最新快照。"""
    import hashlib
    import time

    run_id = _safe_id(run_id)
    _v1_detail(run_id)

    def stream():
        previous = ""
        sequence = 0
        while True:
            try:
                snapshot = _v1_detail(run_id)
                serialized = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
                if digest != previous:
                    sequence += 1
                    yield f"id: {sequence}\nevent: run.snapshot\ndata: {serialized}\n\n"
                    previous = digest
                else:
                    yield ": heartbeat\n\n"
                # 只按已确认的终态关流；派发窗口与等待审批都必须继续推送。
                if snapshot.get("terminal"):
                    return
            except Exception:
                logger.exception("运行事件流读取失败 run_id=%s", run_id)
                yield "event: stream.error\ndata: {\"message\":\"运行快照暂时不可用\"}\n\n"
                return
            time.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


@router.get("/api/v1/runs/{run_id}/control-audit")
def v1_control_audit(run_id: str, _: None = Depends(_authorize)) -> dict[str, Any]:
    run_id = _safe_id(run_id)
    _v1_detail(run_id)
    connection = _control_db()
    try:
        rows = connection.execute(
            "SELECT id,run_id,task_id,action,actor,reason,created_at FROM control_audit WHERE run_id=? ORDER BY id",
            (run_id,),
        ).fetchall()
        return {"items": [dict(row) for row in rows], "total": len(rows)}
    finally:
        connection.close()


class V1StepRunRequest(BaseModel):
    # confirm_write：写步骤必须显式确认，避免误触外部写入。
    confirm_write: bool = False


def _load_model_calls(run_id: str, task_id: str | None = None) -> list[dict[str, Any]]:
    """按行读取 Provider 审计并按任务过滤；损坏行跳过，不伪造数据。"""
    path = _store().run_dir(run_id) / "model_calls.jsonl"
    items: list[dict[str, Any]] = []
    if not path.is_file():
        return items
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("忽略损坏的模型调用审计行 run_id=%s", run_id)
                continue
            if not isinstance(item, dict):
                continue
            if task_id and str(item.get("task_id") or "") != task_id:
                continue
            items.append(item)
    return items


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


@router.get("/api/v1/runs/{run_id}/board")
def v1_run_board(run_id: str, _: None = Depends(_authorize)) -> dict[str, Any]:
    """任务板首屏投影：只返回任务卡摘要与步骤状态，不携带 input/output。"""
    store = _store()
    payload = _run_payload(store, _safe_id(run_id))
    tasks: list[dict[str, Any]] = []
    for task in payload.get("tasks") or []:
        steps = task.get("steps") or []
        report = task.get("report") if isinstance(task.get("report"), dict) else {}
        problem = task.get("problem") if isinstance(task.get("problem"), dict) else {}
        tasks.append({
            "task_id": task.get("task_id", ""),
            "carrier": task.get("carrier", ""),
            "biztype": task.get("biztype", ""),
            "service_name": str(problem.get("service_name") or report.get("service_name") or ""),
            "result": str(task.get("status") or "PENDING"),
            "terminal_step": str(report.get("terminal_step") or ""),
            "terminal_reason": str(report.get("terminal_reason") or ""),
            "next_action": str(report.get("next_action") or ""),
            "approval_pending": bool(task.get("approval_pending")),
            "lifecycle": task.get("lifecycle") if isinstance(task.get("lifecycle"), dict) else {},
            "current_step": str((task.get("lifecycle") or {}).get("current_step") or ""),
            "attempt": int((task.get("lifecycle") or {}).get("attempt") or 0),
            "steps_count": len(steps),
            "duration_ms": report.get("elapsed_ms"),
            "step_statuses": {
                str(step.get("name") or ""): str(step.get("status") or "")
                for step in steps
            },
        })
    return {
        "run_id": str(payload.get("run_id") or run_id),
        "summary": _v1_run_summary(payload),
        "tasks": tasks,
        "total": len(tasks),
    }


@router.post("/api/v1/runs/{run_id}/tasks/{task_id}/retry", status_code=202)
def v1_retry_task(
    run_id: str, task_id: str, background: BackgroundTasks,
    _: None = Depends(_authorize),
) -> dict[str, Any]:
    """Retry one terminal failed task in the same run without replaying already completed tasks."""
    from app.infrastructure.config import load_config, workflow_recursion_limit
    from app.infrastructure.filesystem.workspace import WorkspaceManager
    from app.workflow.graph import TASK_DISPATCH
    from app.workflow.wiring import build_graph, build_services

    run_id, task_id = _safe_id(run_id), _safe_id(task_id)
    store = _store()
    detail = _v1_detail(run_id)
    if detail["status"] not in {"failed", "partial_failed", "succeeded"} or detail.get("worker_active"):
        raise HTTPException(status_code=409, detail="只有已停止的 Run 才能重试单个任务")
    if (store.run_dir(run_id) / "reports" / "run_failure.json").is_file():
        raise HTTPException(status_code=409, detail="该 Run 存在运行级异常，请先使用 Run 级重试；不能只重跑其中一个 Task")

    task_detail = v1_get_task(run_id, task_id, None)
    task_payload = task_detail.get("task") if isinstance(task_detail.get("task"), dict) else {}
    task_result = str(task_payload.get("result") or "").upper()
    if task_result not in {"FAILED", "FAIL"}:
        raise HTTPException(status_code=409, detail=f"当前 Task 结论为 {task_result or 'UNKNOWN'}，只允许重试终态失败任务")
    if task_payload.get("approval_pending"):
        raise HTTPException(status_code=409, detail="该 Task 仍有待审批决策，不能启动整任务重试")
    task_steps = list(task_payload.get("steps") or [])
    if not any(step.get("name") == "finalize_execution" for step in task_steps):
        raise HTTPException(status_code=409, detail="Task 尚未完成终态收尾，不能作为终态失败任务重试")
    if any(step.get("name") == "prepare_delivery" for step in task_steps):
        raise HTTPException(status_code=409, detail="该 Task 已进入 Git 交付阶段；为防止重复提交，请到失败节点详情重试 prepare_delivery")

    with _RUN_LOCKS_GUARD:
        lock = _RUN_LOCKS.setdefault(run_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该 Run 的后台线程仍持有执行锁，暂不能重试")

    baseline_archive: Path | None = None
    baseline_original: Path | None = None
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
        try:
            graph.update_state(cfg, patch, as_node=TASK_DISPATCH)
        except Exception:
            if baseline_archive is not None and baseline_archive.exists() and baseline_original is not None:
                baseline_archive.replace(baseline_original)
            raise

        task_report = store.task_dir(run_id, task_id) / "reports" / "final.json"
        task_report.unlink(missing_ok=True)
        (store.run_dir(run_id) / "reports" / "final.json").unlink(missing_ok=True)
        LifecycleStore(store.root).update_run(
            run_id, status="queued", phase="task_retry_queued", current_step="",
            started_at=None, finished_at=None, error={},
            error_type="", error_message="", terminal_reason="", next_action="",
        )
        LifecycleStore(store.root).update_task(
            run_id, task_id, status="retrying", current_step="retry_prepare",
            attempt=next_attempt, terminal_step="", terminal_reason="", next_action="",
            error={}, last_error={}, last_error_summary="",
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
        _audit_control(
            run_id, task_id, "task.retry_requested", "api",
            f"attempt={next_attempt}; baseline_archived={bool(baseline_archive)}",
        )
    except Exception:
        lock.release()
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
    record = store.load(run_id, step_name, task_id) or store.load(run_id, step_name)
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
    return {
        "run_id": run_id,
        "task_id": task_id,
        "step": record.model_dump(mode="json"),
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
    from app.application.step_runner import StepRunRequest, StepRunner, StepRunnerError

    run_id, task_id, step_name = _safe_id(run_id), _safe_id(task_id), _safe_id(step_name)
    store = _store()
    # Capture the persisted lifecycle before taking the write lock. If status is derived
    # after this endpoint acquires the lock, _v1_detail correctly sees a locked worker and
    # would incorrectly reject this very single-step request as RUN_ACTIVE.
    status_before_lock = str(_v1_detail(run_id).get("status") or "")

    def _status(_value: str) -> str:
        return status_before_lock

    actor = "bearer-token" if authorization else "local-user"
    with _RUN_LOCKS_GUARD:
        run_lock = _RUN_LOCKS.setdefault(run_id, threading.Lock())
    if not run_lock.acquire(blocking=False):
        message = "该执行正在被启动或重跑占用，请稍后重试"
        _audit_control(run_id, task_id, f"step.run.rejected:{step_name}", actor, message)
        raise HTTPException(status_code=409, detail={"code": "RUN_LOCKED", "message": message})
    runner = StepRunner(store, run_status=_status, on_finish=run_lock.release)
    try:
        result = runner.run(StepRunRequest(
            run_id=run_id, task_id=task_id, step_name=step_name,
            confirm_write=request.confirm_write,
        ))
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
