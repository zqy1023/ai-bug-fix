"""运行管理路由。"""
from __future__ import annotations

import json
import threading
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from server.routes.common import *

router = APIRouter()

# 全局串行：同一时刻只允许一个执行批次占用工作流。
_SERIAL_START_LOCK = threading.Lock()
_TERMINAL_RUN_STATUSES = {
    "succeeded", "partial_failed", "failed", "cancelled", "manual", "blocked", "skipped",
    # 人工审批已移除；历史等待审批 Run 不再占用全局串行位。
    "waiting_approval",
}


def _idempotency_lookup(key: str):
    connection = _control_db()
    try:
        return connection.execute(
            "SELECT payload_hash,run_id FROM idempotency_keys WHERE key=?", (key,)
        ).fetchone()
    finally:
        connection.close()


def _busy_run(store) -> tuple[str, str] | None:
    """返回仍在占用全局串行位的批次；已终态批次不阻塞新批次。"""
    from infrastructure.storage.lifecycle import LifecycleStore

    lifecycle = LifecycleStore(store.root)
    for run_id in _all_run_ids(store):
        try:
            status = str(lifecycle.read_run(run_id).get("status") or "").lower()
        except Exception:
            status = ""
        if status:
            if status not in _TERMINAL_RUN_STATUSES:
                return run_id, status
            continue
        # 旧记录或刚持久化、尚未写 lifecycle 的 queued Run 走统一摘要投影。
        try:
            status = str(_v1_run_summary(_run_payload(store, run_id)).get("status") or "")
        except Exception:
            logger.warning("启动前无法读取批次状态 run_id=%s", run_id, exc_info=True)
            continue
        if status not in _TERMINAL_RUN_STATUSES:
            return run_id, status
    return None


def _run_config_payload(*, force: bool = False) -> dict[str, Any]:
    """提供新建运行的有效默认值与上限；设置来源为 Nacos。"""
    from infrastructure.llm.models import model_options, model_options_by_role
    from core.policy import AgentPolicy, resolve_policy
    from config.nacos import load_nacos_config, nacos_target

    config = _config()
    nacos = nacos_target()
    raw = load_nacos_config(force=force)
    overrides: dict[str, Any] = {}
    for key, name in (
        ("LOG_PULL_HOURS", "log_hours"),
        ("LOG_PULL_LIMIT", "log_limit"),
        ("AI_REPAIR_MAX_ATTEMPTS", "max_attempts"),
    ):
        if key in raw:
            try:
                overrides[name] = int(raw[key])
            except (TypeError, ValueError):
                pass
    policy_raw = raw.get("AI_REPAIR_AGENT_POLICY")
    try:
        policy_values = json.loads(policy_raw) if isinstance(policy_raw, str) else policy_raw
    except ValueError:
        policy_values = None
    if isinstance(policy_values, dict):
        if policy_values.get("unified_model"):
            overrides["unified_model"] = str(policy_values["unified_model"])
        if policy_values.get("review_model"):
            overrides["review_model"] = str(policy_values["review_model"])

    defaults = V1StartRunRequest().model_dump(mode="json")
    try:
        defaults["agent_policy"] = resolve_policy()
    except Exception:
        defaults["agent_policy"] = AgentPolicy().model_dump(mode="json")
    return {
        "defaults": defaults,
        "limits": {
            "max_log_hours": 720,
            "max_attempts": 5,
        },
        "models": model_options_by_role(config),
        "model_catalog": [item.as_dict() for item in model_options(config)],
        "overrides": overrides,
        "customized": bool(overrides),
        "nacos": {
            "enabled": bool(nacos["enabled"]),
            "data_id": nacos["data_id"],
            "group": nacos["group"],
        },
    }


@router.get("/api/v1/run-config")
def v1_run_config(_: None = Depends(_authorize)) -> dict[str, Any]:
    return _run_config_payload()


class V1RunDefaultsUpdate(BaseModel):
    """设置页可修改的运行默认值；None 表示清除该 Nacos 覆盖。"""

    model_config = {"extra": "forbid"}

    log_hours: int | None = Field(default=None, ge=1, le=720)
    log_limit: int | None = Field(default=None, ge=0)
    max_attempts: int | None = Field(default=None, ge=1, le=5)
    unified_model: str | None = Field(default=None, max_length=200)
    review_model: str | None = Field(default=None, max_length=200)


def _agent_policy_values() -> dict[str, Any]:
    from config.nacos import nacos_get

    try:
        value = json.loads(nacos_get("AI_REPAIR_AGENT_POLICY", "{}"))
    except ValueError:
        return {}
    return dict(value) if isinstance(value, dict) else {}


@router.put("/api/v1/run-config")
def v1_update_run_config(
    request: V1RunDefaultsUpdate,
    _: None = Depends(_authorize),
) -> dict[str, Any]:
    """写入 Nacos 配置；本地不保存覆盖，写失败直接返回 503。"""
    from infrastructure.llm.models import model_options
    from config.nacos import NacosUnavailable, publish_nacos_values

    updates = request.model_dump(exclude_unset=True)
    catalog = {item.model_id for item in model_options(_config())}
    for key in ("unified_model", "review_model"):
        value = updates.get(key)
        if value and value not in catalog:
            raise HTTPException(status_code=400, detail=f"{key} 不在可用模型目录中")

    nacos_updates: dict[str, Any] = {}
    for source, target in (
        ("log_hours", "LOG_PULL_HOURS"),
        ("log_limit", "LOG_PULL_LIMIT"),
        ("max_attempts", "AI_REPAIR_MAX_ATTEMPTS"),
    ):
        if source in updates:
            nacos_updates[target] = updates[source]

    if "unified_model" in updates or "review_model" in updates:
        policy = _agent_policy_values()
        for source in ("unified_model", "review_model"):
            if source in updates:
                value = updates[source]
                if value:
                    policy[source] = value
                else:
                    policy.pop(source, None)
        nacos_updates["AI_REPAIR_AGENT_POLICY"] = (
            json.dumps(policy, ensure_ascii=False) if policy else None
        )

    try:
        if nacos_updates:
            publish_nacos_values(nacos_updates)
    except NacosUnavailable as exc:
        raise HTTPException(status_code=503, detail=public_exception(exc, prefix="Nacos 设置不可用")) from exc
    return _run_config_payload(force=True)


@router.delete("/api/v1/run-config")
def v1_reset_run_config(_: None = Depends(_authorize)) -> dict[str, Any]:
    """删除设置页写入的 Nacos 键，恢复环境默认。"""
    from config.nacos import NacosUnavailable, publish_nacos_values

    nacos_updates: dict[str, Any] = {
        "LOG_PULL_HOURS": None,
        "LOG_PULL_LIMIT": None,
        "AI_REPAIR_MAX_ATTEMPTS": None,
    }
    policy = _agent_policy_values()
    if "unified_model" in policy or "review_model" in policy:
        policy.pop("unified_model", None)
        policy.pop("review_model", None)
    nacos_updates["AI_REPAIR_AGENT_POLICY"] = (
        json.dumps(policy, ensure_ascii=False) if policy else None
    )
    try:
        publish_nacos_values(nacos_updates)
    except NacosUnavailable as exc:
        raise HTTPException(status_code=503, detail=public_exception(exc, prefix="Nacos 设置不可用")) from exc
    return _run_config_payload(force=True)


@router.get("/api/v1/health")
def v1_health(_: None = Depends(_authorize)) -> dict[str, Any]:
    return _health_payload()


@router.get("/api/v1/dashboard/summary")
def v1_dashboard(_: None = Depends(_authorize)) -> dict[str, Any]:
    store = _store()
    summaries = []
    unreadable_runs = 0
    for run_id in _all_run_ids(store):
        try:
            summaries.append(_v1_run_summary(_run_payload(store, run_id)))
        except (OSError, ValueError, HTTPException):
            unreadable_runs += 1
            logger.warning("仪表盘跳过无法读取的执行 run_id=%s", run_id, exc_info=True)
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
        # 无法读取的记录跳过但不静默：前端据此提示数据可能不完整。
        "unreadable_runs": unreadable_runs,
        "degraded": unreadable_runs > 0,
    }


@router.get("/api/v1/runs")
def v1_list_runs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(
        default=20,
        ge=1,
        le=100,
    ),
    status: str | None = None,
    status_group: str | None = Query(default=None, pattern="^(active|ended)$"),
    keyword: str | None = Query(default=None, max_length=160),
    from_: str | None = Query(default=None, alias="from", max_length=40),
    to: str | None = Query(default=None, max_length=40), _: None = Depends(_authorize),
) -> dict[str, Any]:
    store = _store()
    candidates = []
    unreadable_runs = 0
    from_time, to_time = _parse_time(from_), _parse_time(to)
    active_statuses = {"queued", "running", "pause_requested", "cancel_requested"}
    ended_statuses = {"succeeded", "partial_failed", "failed", "cancelled"}
    for run_id in _all_run_ids(store):
        try:
            item = _v1_run_summary(_run_payload(store, run_id))
        except (OSError, ValueError, HTTPException):
            unreadable_runs += 1
            logger.warning("执行列表跳过无法读取的记录 run_id=%s", run_id, exc_info=True)
            continue
        if keyword and keyword.casefold() not in run_id.casefold():
            continue
        created = _parse_time(item.get("started_at"))
        if from_time and (not created or created < from_time):
            continue
        if to_time and (not created or created > to_time):
            continue
        candidates.append(item)

    # 分组计数覆盖当前时间/检索范围，但不受 status/status_group 过滤影响。
    active_count = sum(1 for item in candidates if item["status"] in active_statuses)
    ended_count = sum(
        1 for item in candidates
        if item.get("terminal") is True or item["status"] in ended_statuses
    )
    attention_count = sum(
        int(item.get("failed_tasks") or 0)
        + int(item.get("blocked_tasks") or 0)
        + int(item.get("manual_tasks") or 0)
        for item in candidates
    )
    attention_run = next(
        (
            item for item in candidates
            if int(item.get("failed_tasks") or 0)
            + int(item.get("blocked_tasks") or 0)
            + int(item.get("manual_tasks") or 0) > 0
        ),
        None,
    )

    filtered = [
        item for item in candidates
        if (not status or item["status"] == status)
        and (
            not status_group
            or (status_group == "active" and item["status"] in active_statuses)
            or (status_group == "ended" and (item.get("terminal") is True or item["status"] in ended_statuses))
        )
    ]
    start = (page - 1) * page_size
    return {
        "items": filtered[start:start + page_size], "total": len(filtered),
        "page": page, "page_size": page_size,
        "unreadable_runs": unreadable_runs,
        "degraded": unreadable_runs > 0,
        "counts": {
            "active": active_count,
            "ended": ended_count,
            "attention": attention_count,
        },
        "attention_run_id": str(attention_run.get("run_id") or "") if attention_run else "",
    }


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

    with _SERIAL_START_LOCK:
        existing = _idempotency_lookup(idempotency_key)
        if existing:
            if existing["payload_hash"] != payload_hash:
                raise HTTPException(status_code=409, detail="相同 Idempotency-Key 不允许复用于不同请求参数")
            return {"run_id": existing["run_id"], "status": "accepted", "idempotent_replay": True}

        busy = _busy_run(_store())
        if busy:
            busy_run_id, busy_status = busy
            raise HTTPException(
                status_code=409,
                detail=(
                    f"全局串行模式：执行批次 {busy_run_id} 当前为 {busy_status}，"
                    "请先完成、取消或处理审批后再新建批次"
                ),
            )

        connection = _control_db()
        run_lock: threading.Lock | None = None
        lock_held = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            # 幂等键在锁外可能已被另一进程写入，事务内再确认一次。
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
                run_lock = _run_lock(run_id)
                if not run_lock.acquire(blocking=False):
                    raise HTTPException(status_code=409, detail="该执行已被另一个工作线程启动")
                lock_held = True
                _STARTED_RUNS.add(run_id)

            try:
                from workflow.engine.entrypoint import prepare_run
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
        from workflow.engine.wiring import build_graph

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
        from workflow.engine.wiring import build_graph

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
    _require_current_workflow(run_id)
    run_id = _safe_id(run_id)
    store = _store()
    detail = _v1_detail(run_id)
    if detail["status"] != "failed":
        raise HTTPException(status_code=409, detail="只有终态失败的 Run 才能重试；活动执行请使用停止或暂停")
    with _RUN_LOCKS_GUARD:
        lock = _run_lock(run_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该 Run 仍被后台工作线程占用，状态正在收敛")

    try:
        checkpoint = detail.get("checkpoint") if isinstance(detail.get("checkpoint"), dict) else {}
        run_meta = store.load_run(run_id)
        if checkpoint.get("resumable"):
            mode = "resume"
            worker = _resume_workflow_background
            # 与 restart 模式同口径：请求即把上一轮失败的报告与生命周期错误
            # 移出当前视图。这里归档而不是删除——恢复失败时
            # _record_background_failure 仍要读回上一轮的任务明细，不能因为
            # 一次失败的重试把历史报告清空。
            reports = store.run_dir(run_id) / "reports"
            for name in ("run_failure.json", "final.json"):
                previous = reports / name
                if previous.is_file():
                    previous.replace(reports / f"{name}.retry_previous")
            LifecycleStore(store.root).update_run(
                run_id, status="retrying", phase="retry_queued", current_step="",
                error={}, error_type="", error_message="", terminal_reason="",
                next_action="", worker_active=True,
                allow_reactivate=True,
            )
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
                allow_reactivate=True,
            )
            overrides = _config().run_parameters(run_meta)
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
    _require_current_workflow(run_id)
    run_id = _safe_id(run_id)
    with _RUN_LOCKS_GUARD:
        lock = _run_lock(run_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该执行正在进行其他控制操作")

    # 检查点和状态必须在取得执行锁后重新读取，避免并发控制请求在校验后改变状态。
    try:
        detail = _v1_detail(run_id)
        # 本控制操作持有的锁也会被投影算作 worker_active，暂停标记此时可能
        # 显示为 pause_requested；锁已保证没有另一个后台执行器，仍可恢复。
        if detail["status"] not in {"running", "queued", "paused", "pause_requested", "failed"} or not detail["checkpoint"]["resumable"]:
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
            paused_lock = _run_lock(run_id)
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
    try:
        _audit_control(run_id, "", "run.cancel_requested", "api")
    except Exception:
        # The durable cancel flag is already committed; audit failure must not tell the
        # operator that cancellation was rejected after the request was accepted.
        logger.exception("写入停止请求审计失败 run_id=%s", run_id)
    return {"run_id": run_id, "status": "cancel_requested", "message": "已记录取消请求；正在执行的节点会在安全边界收尾"}


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
    items = _load_model_calls(run_id, task_id)
    for item in items:
        item["events"] = []

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


@router.get("/api/v1/runs/{run_id}/board")
def v1_run_board(run_id: str, _: None = Depends(_authorize),
                 page: int = Query(default=1, ge=1),
                 page_size: int = Query(default=20, ge=1, le=100),
                 category: str = Query(default="all", pattern="^(all|pending|active|changed_delivered|unchanged_delivered|failed|skipped|paused)$"),
                 keyword: str = Query(default="", max_length=160)) -> dict[str, Any]:
    """任务板首屏投影：只返回任务卡摘要与步骤状态，不携带 input/output。"""
    store = _store()
    payload = _run_payload(store, _safe_id(run_id))
    tasks: list[dict[str, Any]] = []
    for task in payload.get("tasks") or []:
        steps = task.get("steps") or []
        report = task.get("report") if isinstance(task.get("report"), dict) else {}
        problem = task.get("problem") if isinstance(task.get("problem"), dict) else {}
        tasks.append({
            "report": report,
            "steps": steps,
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
    from server.task_board import paginate_tasks
    board = paginate_tasks(tasks, page, page_size, category, keyword)
    # 分类需读取交付证据，但任务板只返回摘要，避免携带全部源码与模型上下文。
    for task in board["tasks"]:
        task.pop("report", None)
        task.pop("steps", None)
    return {
        "run_id": str(payload.get("run_id") or run_id),
        "summary": _v1_run_summary(payload),
        "stages": task_stages(),
        **board,
    }

