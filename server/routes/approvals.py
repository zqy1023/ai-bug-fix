"""发布后的最终人工审批，不恢复旧审批检查点，也不自动合并。"""
from __future__ import annotations

from typing import Any, Literal
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core.contracts import WORKFLOW_VERSION
from core.policy import APPROVAL_RETRY_TARGETS
from infrastructure.storage.final_approval import decide_approval
from server.context import _authorize, _safe_id, _store
from server.routes.common import _all_run_ids

router = APIRouter()


class FinalDecision(BaseModel):
    decision: Literal["approve", "reject", "close"]
    candidate_id: str = Field(min_length=1)
    commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    reason: str = Field(default="", max_length=2000)
    reason_code: Literal["business", "scope", "regression", "evidence", "other"] = "other"
    retry_target: str = "unified_fixer_agent"


@router.get("/api/v1/approvals")
def v1_list_approvals(status: str = Query(default="pending", pattern="^(pending|approved|rejected|closed)$"),
                      _: None = Depends(_authorize)) -> dict[str, Any]:
    store = _store()
    items = []
    for run_id in _all_run_ids(store):
        current = store.load_run(run_id).get("workflow_version") == WORKFLOW_VERSION
        for item in store.database(run_id).states("final_approval"):
            if item.get("status") == status:
                items.append({**item, "approval_id": f"{run_id}:{item['task_id']}",
                              "read_only": not current, "detail_url": f"/runs/{run_id}/tasks/{item['task_id']}/change"})
    return {"items": items, "total": len(items)}


def drain_approval_retries(run_id: str) -> None:
    """空闲时消费持久化驳回队列；忙时保留 pending，不阻塞其他任务。"""
    from infrastructure.storage.lifecycle import LifecycleStore
    from workflow.engine.runner import StepRunnerError
    from server.routes.tasks import v1_run_step
    from server.routes.common import V1StepRunRequest

    store = _store()
    lifecycle = LifecycleStore(store.root)
    life = lifecycle.read_run(run_id)
    if life.get("worker_active") and lifecycle.owns_worker_lease(
        run_id, owner_id=str(life.get("worker_lease_owner") or ""), lease_token=str(life.get("worker_lease_token") or "")):
        return
    for item in store.database(run_id).states("final_approval"):
        if item.get("status") != "rejected" or item.get("retry_status") not in {"pending", "starting"}:
            continue
        if item.get("retry_status") == "starting":
            try:
                started = datetime.fromisoformat(item.get("retry_started_at") or "")
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                if started + timedelta(seconds=30) > datetime.now(timezone.utc):
                    continue
            except ValueError:
                pass
        task_id = item["task_id"]
        if lifecycle.read_task(run_id, task_id).get("manually_closed"):
            continue
        database = store.database(run_id)
        claimed = False
        claim_stamp = datetime.now(timezone.utc).isoformat()

        def claim(current):
            nonlocal claimed
            if (current.get("status") == "rejected" and current.get("commit") == item["commit"]
                    and current.get("retry_status") == item.get("retry_status")
                    and current.get("retry_started_at") == item.get("retry_started_at")):
                claimed = True
                return {**current, "retry_status": "starting", "retry_started_at": claim_stamp}
            return current

        database.update(task_id, "final_approval", claim)
        if not claimed:
            continue
        try:
            v1_run_step(run_id, task_id, item["retry_target"],
                        V1StepRunRequest(confirm_write=True, continue_after=True), None, None)
            database.update(task_id, "final_approval", lambda value: {**value, "retry_status": "queued"}
                            if value.get("commit") == item["commit"] and value.get("retry_started_at") == claim_stamp and value.get("status") == "rejected" else value)
        except (StepRunnerError, HTTPException) as error:
            database.update(task_id, "final_approval", lambda value: {**value, "retry_status": "pending",
                                                                    "retry_error": str(error)}
                            if value.get("commit") == item["commit"] and value.get("retry_started_at") == claim_stamp and value.get("status") == "rejected" else value)
        return


@router.post("/api/v1/approvals/{approval_id}/decision")
def v1_decide_approval(approval_id: str, request: FinalDecision, background: BackgroundTasks,
                      _: None = Depends(_authorize)) -> dict[str, Any]:
    if ":" not in approval_id:
        raise HTTPException(status_code=400, detail="审批身份必须包含 Run 和 Task")
    run_id, task_id = map(_safe_id, approval_id.split(":", 1))
    store = _store()
    if store.load_run(run_id).get("workflow_version") != WORKFLOW_VERSION:
        raise HTTPException(status_code=409, detail="历史审批只读")
    if request.retry_target not in APPROVAL_RETRY_TARGETS:
        raise HTTPException(status_code=422, detail="无效的回退节点")
    try:
        result = decide_approval(store, run_id, task_id, candidate_id=request.candidate_id,
                                 commit=request.commit, decision=request.decision,
                                 reason=request.reason, retry_target=request.retry_target, reason_code=request.reason_code)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if request.decision == "reject":
        background.add_task(drain_approval_retries, run_id)
    return {**result, "message": "审批已记录；合并必须由人工执行"}


@router.post("/api/v1/approvals/{approval_id}/retry", status_code=202)
def v1_retry_approval(approval_id: str, background: BackgroundTasks, _: None = Depends(_authorize)):
    if ":" not in approval_id:
        raise HTTPException(status_code=400, detail="审批身份无效")
    run_id, task_id = map(_safe_id, approval_id.split(":", 1))
    store = _store()
    if store.load_run(run_id).get("workflow_version") != WORKFLOW_VERSION:
        raise HTTPException(status_code=409, detail="历史审批只读")
    value = store.database(run_id).get(task_id, "final_approval") or {}
    if value.get("status") != "rejected" or value.get("retry_status") not in {"pending", "starting"}:
        raise HTTPException(status_code=409, detail="没有待重试的驳回决定")
    background.add_task(drain_approval_retries, run_id)
    return {"status": "retry_requested"}
