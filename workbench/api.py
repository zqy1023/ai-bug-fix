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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
    run_id: str | None = Field(default=None, max_length=160)


class ApprovalRequest(BaseModel):
    decision: str


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
    run_steps = [
        item.model_dump(mode="json")
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
            approval = next((s for s in task_steps if s.get("name") == "request_approval"), None)
            tasks.append({
                "task_id": task_id,
                "carrier": str((report or {}).get("carrier") or _task_identity(slot, "carrier")),
                "biztype": str((report or {}).get("biztype") or _task_identity(slot, "biztype")),
                "status": str((report or {}).get("result") or _task_status(task_steps, approval)),
                "problem": _task_problem(slot),
                "steps": task_steps,
                "report": report if isinstance(report, dict) else {},
                "approval_pending": _approval_pending(approval),
            })
    pending = sum(1 for task in tasks if task["approval_pending"])
    results = [str(task["status"]).upper() for task in tasks]
    created = datetime.fromtimestamp(directory.stat().st_ctime, tz=timezone.utc).isoformat()
    updated = datetime.fromtimestamp(directory.stat().st_mtime, tz=timezone.utc).isoformat()
    status = "waiting_approval" if pending else _derive_run_status(run_steps, tasks)
    return {
        "run_id": run_id,
        "status": status,
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


def _task_status(steps: list[dict[str, Any]], approval: dict[str, Any] | None) -> str:
    if _approval_pending(approval):
        return "WAITING_APPROVAL"
    if any(s.get("status") == "failed" for s in steps):
        return "FAILED"
    if any(s.get("name") == "finalize_execution" for s in steps):
        return "COMPLETED"
    return "RUNNING" if steps else "PENDING"


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
    if tasks and all(task["status"] not in {"RUNNING", "PENDING", "WAITING_APPROVAL"} for task in tasks):
        if any(str(task["status"]).upper() in {"FAILED", "FAIL"} for task in tasks):
            return "failed"
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
                        "problem": task["problem"],
                        "steps_count": len(task["steps"]),
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
                    "approval_step": next((step for step in task["steps"] if step.get("name") == "request_approval"), None),
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
    _: None = Depends(_authorize),
) -> dict[str, Any]:
    decision = request.decision.upper()
    if decision not in {"APPROVE", "REJECT"}:
        raise HTTPException(status_code=422, detail="decision 只能是 APPROVE 或 REJECT")
    run_id, task_id = _safe_id(run_id), _safe_id(task_id)
    store = _store()
    run = _run_payload(store, run_id)
    task = next((item for item in run["tasks"] if item["task_id"] == task_id), None)
    if not task or not task["approval_pending"]:
        raise HTTPException(status_code=409, detail="该任务当前没有待处理的人工审批")
    with _RUN_LOCKS_GUARD:
        lock = _RUN_LOCKS.setdefault(run_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该 run 正在执行恢复操作")
    try:
        from langgraph.types import Command
        from app.workflow.wiring import build_graph

        graph = build_graph(store=store)
        graph.invoke(
            Command(resume=decision),
            config={"configurable": {"thread_id": run_id}, "recursion_limit": 40},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"恢复审批工作流失败：{type(exc).__name__}: {exc}") from exc
    finally:
        lock.release()
    return {"run_id": run_id, "task_id": task_id, "decision": decision, "status": "resumed"}
