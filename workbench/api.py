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

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.infrastructure.config import load_config
from app.infrastructure.records.store import RecordStore

app = FastAPI(
    title="AI Repair Workbench API",
    version="1.0.0",
    description="Run/task/step/artifact observability and human approval API.",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
_RUN_LOCKS: dict[str, threading.Lock] = {}
_RUN_LOCKS_GUARD = threading.Lock()
_STARTED_RUNS: set[str] = set()
logger = logging.getLogger("workbench.api")


class StartRunRequest(BaseModel):
    # run_id：可选的调用方指定执行标识；缺省由服务端生成。
    run_id: str | None = Field(default=None, max_length=160)


class ApprovalRequest(BaseModel):
    # decision：人工审批决定或人工处置动作。
    decision: str
    # reason：人工操作说明，用于审计与终态报告。
    reason: str = ""


class RetryStepRequest(BaseModel):
    reason: str = Field(default="人工重试失败节点", max_length=500)


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
        raise HTTPException(status_code=503, detail=f"运行配置不可用：{exc}") from exc


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
            # API 随后把 MARK_DONE 当作历史登记，根本不恢复 LangGraph checkpoint。
            approval = _latest_approval_step(task_steps)
            carrier = str((report or {}).get("carrier") or _task_identity(slot, "carrier"))
            biztype = str((report or {}).get("biztype") or _task_identity(slot, "biztype"))
            inferred_status = str((report or {}).get("result") or _task_status(task_steps, approval))
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
            tasks.append({
                "task_id": task_id, "carrier": carrier, "biztype": biztype,
                "status": str(report.get("result") or inferred_status),
                "problem": _task_problem(slot), "steps": task_steps,
                "report": report, "approval_pending": _approval_pending(approval),
            })
    pending = sum(1 for task in tasks if task["approval_pending"])
    results = [str(task["status"]).upper() for task in tasks]
    created = datetime.fromtimestamp(directory.stat().st_ctime, tz=timezone.utc).isoformat()
    updated = datetime.fromtimestamp(directory.stat().st_mtime, tz=timezone.utc).isoformat()
    run_failure = _read_json(directory / "reports" / "run_failure.json", {})
    status = "failed" if isinstance(run_failure, dict) and run_failure else ("waiting_approval" if pending else _derive_run_status(run_steps, tasks))
    return {
        "run_id": run_id,
        "status": status,
        "run_error": run_failure if isinstance(run_failure, dict) else {},
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
        if evidence_output.get("legacy_only"):
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
    if _approval_pending(approval):
        return "WAITING_APPROVAL"
    if any(s.get("status") == "failed" for s in steps):
        return "FAILED"
    if any(s.get("name") == "finalize_execution" for s in steps):
        return "COMPLETED"
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


def _derive_run_status(steps: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> str:
    if not steps and not tasks:
        return "pending"
    if any(step.get("status") == "failed" for step in steps):
        return "failed"
    if tasks and all(str(task["status"]).upper() not in {"RUNNING", "PENDING", "WAITING_APPROVAL", "QUEUED"} for task in tasks):
        task_results = [str(task["status"]).upper() for task in tasks]
        failed = any(result in {"FAILED", "FAIL", "REJECTED"} for result in task_results)
        manual = any(result in {"MANUAL", "MANUAL_REVIEW_REQUIRED", "INSUFFICIENT_EVIDENCE"} for result in task_results)
        succeeded = any(result in {"SUCCESS", "SUCCEEDED", "COMPLETED"} for result in task_results)
        if manual:
            # 人工介入不是成功；避免把包含 MANUAL 任务的整轮执行误标为已完成。
            return "partial_failed"
        if failed:
            return "partial_failed" if succeeded else "failed"
        return "completed"
    if not tasks and any(step.get("name") == "identify_carrier" and step.get("status") in {"succeeded", "skipped"} for step in steps):
        return "completed"
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


def _run_workflow_background(run_id: str) -> None:
    from app.entrypoint import run_workflow

    try:
        run_workflow(run_id)
    except Exception:
        # 保留完整异常堆栈，避免后台任务失败后只留下一个看似仍在运行的 run。
        logger.exception("工作流后台执行失败 run_id=%s", run_id)
        return


@app.get("/api/health")
def health(_: None = Depends(_authorize)) -> dict[str, Any]:
    config = _config()
    return {
        "status": "ok",
        "service": "ai-repair-workbench",
        "version": app.version,
        "runtime_dir_configured": bool(config.runtime_dir),
        "source_repo_configured": config.source_repo is not None,
        "api_token_required": bool(os.getenv("WORKBENCH_API_TOKEN", "").strip()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/runs")
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


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, _: None = Depends(_authorize)) -> dict[str, Any]:
    return _run_payload(_store(), _safe_id(run_id))


@app.get("/api/approvals")
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


@app.post("/api/runs", status_code=202)
def start_run(
    request: StartRunRequest,
    background: BackgroundTasks,
    _: None = Depends(_authorize),
) -> dict[str, Any]:
    config = _config()
    if config.source_repo is None:
        raise HTTPException(status_code=409, detail="未配置 AI_REPAIR_SOURCE_REPO，不能启动生产工作流")
    run_id = _safe_id(request.run_id or uuid.uuid4().hex)
    store = _store()
    run_dir = store.run_dir(run_id)
    with _RUN_LOCKS_GUARD:
        if run_id in _STARTED_RUNS or (run_dir / "run.json").exists():
            raise HTTPException(status_code=409, detail="该 run_id 已存在或已提交；请使用新的 run_id，禁止重复启动覆盖")
        _STARTED_RUNS.add(run_id)
    background.add_task(_run_workflow_background, run_id)
    return {"run_id": run_id, "status": "accepted", "message": "已提交后台执行；请在运行详情中观察节点进度"}


@app.get("/api/runs/{run_id}/artifacts")
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


@app.get("/api/runs/{run_id}/artifacts/content")
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


@app.post("/api/runs/{run_id}/tasks/{task_id}/approval")
def decide_approval(
    run_id: str,
    task_id: str,
    request: ApprovalRequest,
    background: BackgroundTasks,
    _: None = Depends(_authorize),
) -> dict[str, Any]:
    decision = request.decision.upper()
    if decision not in {"APPROVE", "REJECT", "RETRY_LOCATE", "CONTINUE_REPAIR", "MARK_DONE", "TERMINATE"}:
        raise HTTPException(status_code=422, detail="审批或人工处置决定无效")
    if decision in {"CONTINUE_REPAIR", "MARK_DONE", "TERMINATE"} and not request.reason.strip():
        raise HTTPException(status_code=422, detail="继续修复、人工完成或终止操作必须填写处置说明")
    run_id, task_id = _safe_id(run_id), _safe_id(task_id)
    store = _store()
    run = _run_payload(store, run_id)
    task = next((item for item in run["tasks"] if item["task_id"] == task_id), None)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    steps = task.get("steps") if isinstance(task.get("steps"), list) else []
    approval_step = _latest_approval_step(steps)
    approval_input = approval_step.get("input") if isinstance(approval_step, dict) and isinstance(approval_step.get("input"), dict) else {}
    mode = str(approval_input.get("mode") or "change_approval")
    if not task.get("approval_pending") and decision in {"MARK_DONE", "TERMINATE"}:
        report = task.get("report") if isinstance(task.get("report"), dict) else {}
        terminal = str(report.get("result") or task.get("status") or "").upper()
        if terminal not in {"MANUAL", "INSUFFICIENT_EVIDENCE"}:
            raise HTTPException(status_code=409, detail="该任务不是待处置的人工介入任务")
        now = datetime.now(timezone.utc).isoformat()
        store.write_artifact(run_id, "reports/manual_resolution.json", {
            "status": "completed" if decision == "MARK_DONE" else "terminated",
            "decision": decision, "reason": request.reason, "resolved_at": now,
        }, task_id)
        return {"run_id": run_id, "task_id": task_id, "decision": decision, "status": "resolved"}
    manual_actions = {"RETRY_LOCATE", "CONTINUE_REPAIR", "MARK_DONE", "TERMINATE"}

    if mode == "manual_intervention":
        allowed = {"RETRY_LOCATE", "MARK_DONE", "TERMINATE"}
        if approval_input.get("continue_allowed") is True:
            allowed.add("CONTINUE_REPAIR")
        if decision not in allowed:
            raise HTTPException(status_code=422, detail="人工处置动作无效，或当前证据不允许继续修复")
    elif decision not in {"APPROVE", "REJECT"}:
        raise HTTPException(status_code=422, detail="变更审批 decision 只能是 APPROVE 或 REJECT")

    if not task.get("approval_pending"):
        raise HTTPException(status_code=409, detail="该任务当前没有可恢复的人工决策中断；历史终态任务不能原地续跑")

    with _RUN_LOCKS_GUARD:
        lock = _RUN_LOCKS.setdefault(run_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该 run 正在执行恢复操作")
    fingerprint = str(approval_input.get("approval_fingerprint") or "")
    resume_payload = {"decision": decision, "approval_fingerprint": fingerprint}
    # 审批 API 只负责校验决定并排入恢复任务。恢复过程可能继续执行多个 carrier，
    # 不应占用 HTTP 请求；同一个 run 的锁交给后台恢复器持有，直至整张父图完成。
    background.add_task(
        _resume_workflow_background,
        run_id,
        lock,
        resume_payload,
        {"mode": mode, "task_id": task_id, "decision": decision, "reason": request.reason},
    )
    return {"run_id": run_id, "task_id": task_id, "decision": decision, "status": "resume_requested"}

# Versioned control-plane contract consumed by frontend/src/lib/api.ts.
# Legacy /api routes remain available for existing local tooling.
from hashlib import sha256
from typing import Literal


class V1StartRunRequest(BaseModel):
    # log_days：本轮向 ES 回溯的天数，范围 1–30。
    log_days: int = Field(default=1, ge=1, le=30)
    # log_limit：本轮最多读取的日志数；0 表示采用服务端配置。
    log_limit: int = Field(default=0, ge=0, le=10000)
    # review_mode：agent 表示 AI 审查，human 表示强制人工审查。
    review_mode: Literal["agent", "human"] = "agent"
    # max_attempts：单任务修复尝试总次数，包含首次执行。
    max_attempts: int = Field(default=3, ge=1, le=5)
    # 仅供 Task 重跑使用；普通 Run 保持空字符串。
    task_filter_carrier: str = Field(default="", max_length=160)
    task_filter_biztype: str = Field(default="", max_length=160)


class V1ApprovalDecision(BaseModel):
    # decision：普通审批仅使用 approve/reject；人工介入可选择重定位、继续修复、标记完成或终止。
    decision: Literal["approve", "reject", "retry_locate", "continue_repair", "mark_done", "terminate"]
    # reason：审批说明或拒绝原因，最多 1000 字符。
    reason: str = Field(default="", max_length=1000)


def _control_db():
    """Durable idempotency, stop-request and audit metadata, separate from workflow artifacts."""
    import sqlite3
    runtime_dir = _config().runtime_dir
    runtime_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(runtime_dir / "workbench-control.sqlite3"), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
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
        all_steps = list(payload.get("steps") or []) + [
            step for task in (payload.get("tasks") or []) for step in (task.get("steps") or [])
        ]
        active = any(step.get("status") in {"running", "waiting"} for step in all_steps)
        paused_step = any(step.get("status") == "waiting" for step in all_steps)
        if control and control["cancel_requested"]:
            status = "cancel_requested" if active else "cancelled"
        elif control and control["pause_requested"]:
            status = "paused" if paused_step else "pause_requested"
    except Exception:
        logger.debug("读取执行控制状态失败 run_id=%s", run_id, exc_info=True)
    return {
        "run_id": run_id, "status": status,
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
            "steps_count": len(task_steps),
            "duration_ms": report.get("elapsed_ms"),
            "approval_pending": bool(task.get("approval_pending")),
        })
    resumable, next_nodes = False, []
    try:
        from app.workflow.wiring import build_graph
        graph = build_graph(store=store)
        snapshot = graph.get_state({"configurable": {"thread_id": run_id}})
        next_nodes = [str(item) for item in (getattr(snapshot, "next", ()) or ())]
        resumable = bool(next_nodes) and not any(task.get("approval_pending") for task in payload.get("tasks") or [])
    except Exception:
        logger.debug("无法读取执行 checkpoint run_id=%s", run_id, exc_info=True)
    status = _v1_status(str(payload.get("status") or ""))
    connection = _control_db()
    try:
        row = connection.execute(
            "SELECT pause_requested,cancel_requested FROM run_control WHERE run_id=?", (run_id,)
        ).fetchone()
        if row and row["cancel_requested"]:
            status = "cancelled" if not next_nodes else "cancel_requested"
        elif row and row["pause_requested"]:
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
    return {
        **summary, "status": status,
        "config": {
            "log_days": run_meta.get("log_days", 1), "log_limit": run_meta.get("log_limit", 0),
            "review_mode": run_meta.get("review_mode", "agent"), "max_attempts": run_meta.get("max_attempts", 3),
        },
        "steps": steps, "tasks": tasks, "artifacts": artifacts,
        "run_report": run_report if isinstance(run_report, dict) else {},
        "run_error": payload.get("run_error") or {},
        "checkpoint": {"resumable": resumable, "next_nodes": next_nodes},
    }


@app.get("/api/v1/health")
def v1_health(_: None = Depends(_authorize)) -> dict[str, Any]:
    return health()


@app.get("/api/v1/dashboard/summary")
def v1_dashboard(_: None = Depends(_authorize)) -> dict[str, Any]:
    store = _store()
    summaries = []
    for run_id in _all_run_ids(store):
        try:
            summaries.append(_v1_run_summary(_run_payload(store, run_id)))
        except (OSError, ValueError, HTTPException):
            continue
    now = datetime.now(timezone.utc)
    day_start = now - timedelta(hours=24)
    week_start = now.date() - timedelta(days=6)
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


@app.get("/api/v1/runs")
def v1_list_runs(
    page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
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


@app.post("/api/v1/runs", status_code=202)
def v1_start_run(
    request: V1StartRunRequest, background: BackgroundTasks,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=200),
    _: None = Depends(_authorize),
) -> dict[str, Any]:
    config = _config()
    if config.source_repo is None:
        raise HTTPException(status_code=409, detail="未配置 AI_REPAIR_SOURCE_REPO，不能启动生产工作流")
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
                detail=f"初始化执行记录失败，工作流未启动：{type(exc).__name__}: {exc}",
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


def _record_background_failure(run_id: str, error: Exception) -> None:
    """将图外异常写成可查询的终态产物，避免后台任务失败后 Run 永久显示运行中。"""
    try:
        directory = _store().run_dir(_safe_id(run_id))
        if not directory.is_dir():
            return
        report_dir = directory / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        target = report_dir / "run_failure.json"
        temporary = report_dir / "run_failure.json.tmp"
        now = datetime.now(timezone.utc).isoformat()
        reason = f"{type(error).__name__}: {str(error).strip()}"[:2000]
        payload = {
            "status": "FAILED", "error_type": type(error).__name__,
            "message": reason or "后台工作流异常，请检查服务端日志并按 run_id 查询完整堆栈。",
            "terminal_step": "workflow_startup", "terminal_reason": reason or "后台工作流在初始化阶段异常退出。",
            "next_action": "检查配置、工作流图装配错误和服务端异常堆栈，修复后重新执行。",
            "failed_at": now,
        }
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
    try:
        run_workflow(run_id, overrides=overrides, prepared=True)
    except Exception as exc:
        logger.exception("工作流后台执行失败 run_id=%s", run_id)
        _record_background_failure(run_id, exc)
    finally:
        run_lock.release()


@app.get("/api/v1/runs/{run_id}")
def v1_get_run(run_id: str, _: None = Depends(_authorize)) -> dict[str, Any]:
    return _v1_detail(run_id)


def _pending_checkpoint_nodes(snapshot: Any, seen: set[int] | None = None) -> set[str]:
    """递归读取父图/嵌套任务子图的待执行节点；不把已完成的节点误判为可重试。"""
    if snapshot is None:
        return set()
    seen = seen or set()
    marker = id(snapshot)
    if marker in seen:
        return set()
    seen.add(marker)
    nodes = {str(name) for name in (getattr(snapshot, "next", ()) or ())}
    for task in (getattr(snapshot, "tasks", ()) or ()):
        nested = getattr(task, "state", None)
        if nested is not None and not isinstance(nested, (str, bytes, dict)):
            nodes.update(_pending_checkpoint_nodes(nested, seen))
    return nodes


def _retryable_step_names(run_id: str, store: RecordStore, steps: list[dict[str, Any]]) -> list[str]:
    """只有 checkpoint 明确把该失败节点列为待执行时，才开放节点级重试。"""
    failed = {
        str(step.get("name") or "")
        for step in steps
        if step.get("status") == "failed" and str(step.get("name") or "")
    }
    if not failed:
        return []
    try:
        from app.workflow.wiring import build_graph
        graph = build_graph(store=store)
        snapshot = graph.get_state(
            {"configurable": {"thread_id": run_id}},
            subgraphs=True,
        )
        return sorted(failed.intersection(_pending_checkpoint_nodes(snapshot)))
    except Exception:
        logger.debug("无法读取节点级重试 checkpoint run_id=%s", run_id, exc_info=True)
        return []


@app.post("/api/v1/runs/{run_id}/rerun", status_code=202)
def v1_rerun_run(
    run_id: str, background: BackgroundTasks, _: None = Depends(_authorize),
) -> dict[str, Any]:
    """以新 Run ID 重跑本轮；保留原运行记录，不复用旧 checkpoint/工作区。"""
    run_id = _safe_id(run_id)
    detail = _v1_detail(run_id)
    config = detail.get("config") if isinstance(detail.get("config"), dict) else {}
    request = V1StartRunRequest(
        log_days=int(config.get("log_days") or 1),
        log_limit=int(config.get("log_limit") or 0),
        review_mode=config.get("review_mode") if config.get("review_mode") in {"agent", "human"} else "agent",
        max_attempts=int(config.get("max_attempts") or 3),
    )
    result = v1_start_run(
        request, background,
        idempotency_key="rerun-" + uuid.uuid4().hex,
    )
    new_run_id = str(result.get("run_id") or "")
    try:
        _audit_control(new_run_id, "", "run.rerun_from:" + run_id, "api", "从历史 Run 创建全新执行；旧 checkpoint、工作区和产物不复用")
    except Exception:
        logger.exception("记录重跑来源失败 source_run_id=%s new_run_id=%s", run_id, new_run_id)
    return {**result, "source_run_id": run_id, "message": "已创建新的 Run 并重新扫描日志；原 Run 与其产物保持不变"}


@app.get("/api/v1/runs/{run_id}/tasks/{task_id}")
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
        error = last.get("error") if isinstance(last.get("error"), dict) else {}
        detail = str(error.get("message") or last.get("summary") or "")
        report = {
            "result": source.get("status") or "INCOMPLETE",
            "terminal_step": str(last.get("name") or "workflow"),
            "terminal_reason": (
                "历史任务没有持久化终态报告；当前只能根据已有步骤记录展示结果。"
                + (f" 最后一步记录：{detail}" if detail else "")
            ),
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
    run_status = _v1_run_summary(payload)["status"]
    retryable_steps = _retryable_step_names(run_id, store, steps) if run_status == "failed" and not source.get("approval_pending") else []
    return {
        "run_id": run_id,
        "run_status": run_status,
        "retryable_steps": retryable_steps,
        "task": {
            "task_id": task_id, "carrier": source.get("carrier", ""),
            "biztype": source.get("biztype", ""), "result": source.get("status", "PENDING"),
            "problem": source.get("problem") or {}, "approval_pending": bool(source.get("approval_pending")),
            "steps": steps, "report": report,
        },
        "artifacts": artifacts,
        "config": {
            "log_days": run_meta.get("log_days", 1), "log_limit": run_meta.get("log_limit", 0),
            "review_mode": run_meta.get("review_mode", "agent"), "max_attempts": run_meta.get("max_attempts", 3),
        },
    }


@app.post("/api/v1/runs/{run_id}/tasks/{task_id}/rerun", status_code=202)
def v1_rerun_task(
    run_id: str, task_id: str, background: BackgroundTasks,
    _: None = Depends(_authorize),
) -> dict[str, Any]:
    """用新 Run 重新扫描并仅执行同一 carrier + biztype 的任务，不复用旧 checkpoint/工作区。"""
    run_id, task_id = _safe_id(run_id), _safe_id(task_id)
    detail = _v1_detail(run_id)
    source = next((item for item in detail.get("tasks", []) if item.get("task_id") == task_id), None)
    if source is None:
        raise HTTPException(status_code=404, detail="该 Run 中不存在此 Task")
    carrier = str(source.get("carrier") or "").strip()
    biztype = str(source.get("biztype") or "").strip()
    if not carrier or not biztype:
        raise HTTPException(status_code=409, detail="任务缺少明确 carrier/biztype，拒绝进行范围不确定的重跑")
    config = detail.get("config") if isinstance(detail.get("config"), dict) else {}
    request = V1StartRunRequest(
        log_days=int(config.get("log_days") or 1),
        log_limit=int(config.get("log_limit") or 0),
        review_mode=config.get("review_mode") if config.get("review_mode") in {"agent", "human"} else "agent",
        max_attempts=int(config.get("max_attempts") or 3),
        task_filter_carrier=carrier,
        task_filter_biztype=biztype,
    )
    result = v1_start_run(request, background, idempotency_key="rerun-task-" + uuid.uuid4().hex)
    new_run_id = str(result.get("run_id") or "")
    try:
        _audit_control(new_run_id, "", "task.rerun_from:" + run_id + ":" + task_id, "api",
                       f"仅重新扫描并执行 carrier={carrier}, biztype={biztype}；旧工作区与产物不复用")
    except Exception:
        logger.exception("记录 Task 重跑来源失败 source_run_id=%s task_id=%s", run_id, task_id)
    return {
        **result, "source_run_id": run_id, "source_task_id": task_id,
        "carrier": carrier, "biztype": biztype,
        "message": f"已创建新 Run，仅筛选 {carrier} / {biztype} 任务；会重新扫描日志，不复用旧工作区和 checkpoint",
    }


@app.post("/api/v1/runs/{run_id}/tasks/{task_id}/steps/{step_name}/retry", status_code=202)
def v1_retry_failed_step(
    run_id: str,
    task_id: str,
    step_name: str,
    request: RetryStepRequest,
    background: BackgroundTasks,
    authorization: str | None = Header(default=None),
    _: None = Depends(_authorize),
) -> dict[str, Any]:
    """重试 checkpoint 中仍处于 pending 的失败节点；成功后由原图继续正常路由。"""
    run_id, task_id, step_name = _safe_id(run_id), _safe_id(task_id), _safe_id(step_name)
    with _RUN_LOCKS_GUARD:
        lock = _RUN_LOCKS.setdefault(run_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该执行正在进行其他控制操作")
    try:
        store = _store()
        payload = _run_payload(store, run_id)
        source = next((item for item in payload.get("tasks", []) if item.get("task_id") == task_id), None)
        if source is None:
            raise HTTPException(status_code=404, detail="该执行中不存在此任务")
        if str(payload.get("status") or "").lower() != "failed":
            raise HTTPException(status_code=409, detail="只有执行失败的 Run 可以进行节点级重试")
        if source.get("approval_pending"):
            raise HTTPException(status_code=409, detail="任务正在等待人工审批，不能同时重试节点")
        step = next((item for item in reversed(source.get("steps") or []) if item.get("name") == step_name), None)
        if not step or step.get("status") != "failed":
            raise HTTPException(status_code=409, detail="指定节点没有失败记录，拒绝重试")
        retryable = _retryable_step_names(run_id, store, list(source.get("steps") or []))
        if step_name not in retryable:
            raise HTTPException(
                status_code=409,
                detail="该节点不是 LangGraph checkpoint 中待重新执行的节点；为避免跳过依赖或重复执行副作用，未提交重试",
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
        actor = "bearer-token" if authorization else "local-user"
        _audit_control(run_id, task_id, "step.retry_requested:" + step_name, actor, request.reason)
    except HTTPException:
        lock.release()
        raise
    except Exception as exc:
        lock.release()
        logger.exception("提交失败节点重试失败 run_id=%s task_id=%s step=%s", run_id, task_id, step_name)
        raise HTTPException(status_code=503, detail="无法验证或提交节点重试，请检查服务端日志") from exc
    background.add_task(_resume_workflow_background, run_id, lock)
    return {
        "run_id": run_id,
        "task_id": task_id,
        "step_name": step_name,
        "status": "retry_requested",
        "message": "已提交该失败节点重试；节点成功后工作流将按原有路由继续执行",
    }


@app.post("/api/v1/runs/{run_id}/pause", status_code=202)
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


@app.post("/api/v1/runs/{run_id}/resume", status_code=202)
def v1_resume_run(run_id: str, background: BackgroundTasks, _: None = Depends(_authorize)) -> dict[str, Any]:
    run_id = _safe_id(run_id)
    detail = _v1_detail(run_id)
    if detail["status"] not in {"running", "queued", "paused", "failed"} or not detail["checkpoint"]["resumable"]:
        raise HTTPException(status_code=409, detail="执行当前没有可恢复的 checkpoint")
    with _RUN_LOCKS_GUARD:
        lock = _RUN_LOCKS.setdefault(run_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该执行正在进行其他控制操作")
    try:
        connection = _control_db()
    except Exception:
        lock.release()
        raise
    try:
        connection.execute(
            "INSERT INTO run_control(run_id,pause_requested,cancel_requested,updated_at) VALUES(?,0,0,?) "
            "ON CONFLICT(run_id) DO UPDATE SET pause_requested=0,updated_at=excluded.updated_at",
            (run_id, datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
    finally:
        connection.close()
    background.add_task(_resume_workflow_background, run_id, lock)
    _audit_control(run_id, "", "run.resume_requested", "api")
    return {"run_id": run_id, "status": "resume_requested"}


def _resume_workflow_background(
    run_id: str,
    run_lock: Any = None,
    resume_payload: dict[str, Any] | None = None,
    approval_meta: dict[str, Any] | None = None,
) -> None:
    if run_lock is None:
        with _RUN_LOCKS_GUARD:
            run_lock = _RUN_LOCKS.setdefault(run_id, threading.Lock())
        if not run_lock.acquire(blocking=False):
            logger.warning("跳过重复恢复的工作流 run_id=%s", run_id)
            return
    try:
        from app.workflow.wiring import build_graph
        store = _store()
        graph = build_graph(store=store)
        config = {"configurable": {"thread_id": run_id}, "recursion_limit": 40}
        snapshot = graph.get_state(config)
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
        if isinstance(stale_payload, dict) and stale_payload.get("terminal_step") == "workflow_startup" and stale_payload.get("result") == "FAILED":
            stale_run_report.unlink(missing_ok=True)
        from app.entrypoint import _write_run_report_if_terminal
        _write_run_report_if_terminal(store, run_id, graph, final_state=final_state)
        if isinstance(approval_meta, dict) and approval_meta.get("mode") == "manual_intervention" and approval_meta.get("decision") in {"MARK_DONE", "TERMINATE"}:
            task_id = str(approval_meta.get("task_id") or "")
            store.write_artifact(run_id, "reports/manual_resolution.json", {
                "status": "completed" if approval_meta.get("decision") == "MARK_DONE" else "terminated",
                "decision": approval_meta.get("decision"),
                "reason": str(approval_meta.get("reason") or ""),
                "resolved_at": datetime.now(timezone.utc).isoformat(),
            }, task_id)
    except Exception as exc:
        logger.exception("恢复工作流失败 run_id=%s", run_id)
        _record_background_failure(run_id, exc)
    finally:
        run_lock.release()


@app.post("/api/v1/runs/{run_id}/cancel", status_code=202)
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


@app.get("/api/v1/approvals")
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
        if str(approval_input.get("mode") or "") == "manual_intervention":
            continue
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
            "requires_human_authorization": bool(plan.get("requires_human_authorization") or scope == "project"),
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


@app.get("/api/v1/manual-interventions")
def v1_list_manual_interventions(_: None = Depends(_authorize)) -> dict[str, Any]:
    """列出需要人工决定的任务，包括挂起中的定位处置，不只提供查看链接。"""
    store = _store()
    items: list[dict[str, Any]] = []
    for run_id in _all_run_ids(store):
        try:
            payload = _run_payload(store, run_id)
        except (HTTPException, OSError, ValueError):
            continue
        for task in payload.get("tasks") or []:
            task_id = str(task.get("task_id") or "")
            resolution_path = store.task_dir(run_id, task_id) / "reports" / "manual_resolution.json"
            resolution = _read_json(resolution_path, {})
            if isinstance(resolution, dict) and resolution.get("status") in {"completed", "terminated"}:
                continue
            steps = task.get("steps") if isinstance(task.get("steps"), list) else []
            approval = _latest_approval_step(steps)
            approval_input = approval.get("input") if isinstance(approval, dict) and isinstance(approval.get("input"), dict) else {}
            manual_pending = bool(task.get("approval_pending")) and str(approval_input.get("mode") or "") == "manual_intervention"
            if task.get("approval_pending") and not manual_pending:
                continue
            report = task.get("report") if isinstance(task.get("report"), dict) else {}
            terminal = str(report.get("result") or task.get("status") or "").upper()
            if not manual_pending and terminal not in {"MANUAL", "INSUFFICIENT_EVIDENCE"}:
                continue
            locate_step = next((step for step in reversed(steps) if step.get("name") == "locate_agent"), None)
            locate_output = locate_step.get("output") if isinstance(locate_step, dict) and isinstance(locate_step.get("output"), dict) else {}
            report_step = str(report.get("terminal_step") or approval_input.get("intervention_step") or ("locate_agent" if manual_pending else "")).strip()
            reason = str(approval_input.get("intervention_reason") or report.get("terminal_reason") or "").strip()
            evidence_gaps = locate_output.get("evidence_gaps")
            if not reason and isinstance(evidence_gaps, list) and evidence_gaps:
                # 具体的路径/契约失败比通用 summary 更有操作价值，必须优先展示。
                reason = "定位输出契约校验失败：" + "；".join(str(item) for item in evidence_gaps[:8])
            if not reason:
                reason = str(locate_output.get("summary") or "").strip()
            if not reason:
                reason = "自动流程因安全门禁无法继续，需要人工排查；历史记录没有保存更具体的原因。"
            continue_allowed = bool(approval_input.get("continue_allowed")) if manual_pending else False
            decision_options = ["RETRY_LOCATE", "MARK_DONE", "TERMINATE"] if manual_pending else ["MARK_DONE", "TERMINATE"]
            if manual_pending and continue_allowed:
                decision_options.insert(1, "CONTINUE_REPAIR")
            items.append({
                "intervention_id": f"{run_id}:{task_id}", "run_id": run_id, "task_id": task_id,
                "carrier": str(task.get("carrier") or ""), "biztype": str(task.get("biztype") or ""),
                "step": report_step or str((locate_step or {}).get("name") or "finalize_execution"),
                "status": "waiting_decision" if manual_pending else "open", "summary": reason[:2000],
                "root_cause": str(approval_input.get("root_cause") or locate_output.get("root_cause") or ""),
                "candidate_files": [str(item) for item in (approval_input.get("candidate_files") or locate_output.get("candidate_files") or [])],
                "continue_allowed": continue_allowed, "decision_options": decision_options,
                "created_at": str(payload.get("updated_at") or payload.get("created_at") or ""),
            })
    return {"items": items, "total": len(items)}

@app.post("/api/v1/approvals/{approval_id}/decision")
def v1_decide_approval(
    approval_id: str, request: V1ApprovalDecision, background: BackgroundTasks,
    authorization: str | None = Header(default=None),
    _: None = Depends(_authorize),
) -> dict[str, Any]:
    if ":" not in approval_id:
        raise HTTPException(status_code=400, detail="无效的审批 ID")
    run_id, task_id = approval_id.split(":", 1)
    run_id, task_id = _safe_id(run_id), _safe_id(task_id)
    actor = "bearer-token" if authorization else "local-user"
    _audit_control(run_id, task_id, "approval." + request.decision, actor, request.reason)
    return decide_approval(run_id, task_id, ApprovalRequest(decision=request.decision.upper(), reason=request.reason), background=background)


@app.post("/api/v1/manual-interventions/{intervention_id}/decision")
def v1_decide_manual_intervention(
    intervention_id: str, request: V1ApprovalDecision, background: BackgroundTasks,
    authorization: str | None = Header(default=None),
    _: None = Depends(_authorize),
) -> dict[str, Any]:
    """对人工介入任务执行安全处置；继续修复仅在当前 interrupt 证据允许时放行。"""
    if ":" not in intervention_id:
        raise HTTPException(status_code=400, detail="无效的人工介入 ID")
    run_id, task_id = intervention_id.split(":", 1)
    run_id, task_id = _safe_id(run_id), _safe_id(task_id)
    decision = request.decision.upper()
    if decision not in {"RETRY_LOCATE", "CONTINUE_REPAIR", "MARK_DONE", "TERMINATE"}:
        raise HTTPException(status_code=422, detail="人工介入动作无效")
    if decision in {"CONTINUE_REPAIR", "MARK_DONE", "TERMINATE"} and not request.reason.strip():
        raise HTTPException(status_code=422, detail="继续修复、人工完成或终止操作必须填写处置说明")
    actor = "bearer-token" if authorization else "local-user"
    _audit_control(run_id, task_id, "manual_intervention." + decision.lower(), actor, request.reason)
    return decide_approval(run_id, task_id, ApprovalRequest(decision=decision, reason=request.reason), background=background)

@app.get("/api/v1/runs/{run_id}/artifacts")
def v1_list_artifacts(run_id: str, _: None = Depends(_authorize)) -> dict[str, Any]:
    detail = _v1_detail(run_id)
    return {"items": detail["artifacts"], "total": len(detail["artifacts"])}


@app.get("/api/v1/runs/{run_id}/artifacts/content")
def v1_artifact_content(
    run_id: str, path: str = Query(..., min_length=1, max_length=500), task_id: str = "",
    _: None = Depends(_authorize),
) -> JSONResponse:
    return get_artifact_content(run_id, path, task_id)


@app.get("/api/v1/runs/{run_id}/model-calls")
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


@app.get("/api/v1/runs/{run_id}/events")
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
                if snapshot.get("status") in {"succeeded", "failed", "cancelled"}:
                    return
            except Exception:
                logger.exception("运行事件流读取失败 run_id=%s", run_id)
                yield "event: stream.error\ndata: {\"message\":\"运行快照暂时不可用\"}\n\n"
                return
            time.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


@app.get("/api/v1/runs/{run_id}/control-audit")
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
