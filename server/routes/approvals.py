"""审批控制路由。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from server.routes.common import *

router = APIRouter()

@router.get("/api/v1/approvals")
def v1_list_approvals(
    status: str = Query(default="pending", pattern="^(pending|approved|rejected|expired)$"),
    _: None = Depends(_authorize),
) -> dict[str, Any]:
    pending = _pending_approvals()
    store = _store()
    run_cache: dict[str, dict[str, Any]] = {}
    items = []
    for item in pending.get("items", []):
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
        repair_step = next((value for value in reversed(steps) if value.get("name") == "unified_fixer_agent"), {})
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
        candidate_steps = [_formal_step_payload(store, run_id, task_id, value) for value in steps
                           if value.get("name") in {"generate_report", "submit_changes", "prepare_delivery", "publish_delivery"}]
        candidate = delivery_view(candidate_steps)
        from core.contracts import WORKFLOW_VERSION
        from server.view_models import ActionAvailability
        metadata = store.load_run(run_id)
        current = metadata.get("workflow_version") == WORKFLOW_VERSION
        approve = ActionAvailability(
            enabled=current and candidate.phase in {"prepared", "published"} and bool(candidate.candidate_id),
            reason="" if current and candidate.phase in {"prepared", "published"} and candidate.candidate_id else "缺少有效交付候选或记录属于历史工作流",
        )
        reject = ActionAvailability(enabled=current, reason="" if current else "历史工作流只读，请创建新执行")
        items.append({
            "delivery": candidate.model_dump(mode="json"),
            "actions": {"approve": approve.model_dump(mode="json"), "reject": reject.model_dump(mode="json")},
            "approval_id": f"{run_id}:{task_id}", "run_id": run_id, "task_id": task_id,
            "carrier": item.get("carrier") or "", "biztype": item.get("biztype") or "",
            "scope": safe_plan["scope"], "status": "pending", "summary": summary,
            "changed_files": list(dict.fromkeys(str(value) for value in files if str(value).strip())),
            "change_plan": safe_plan,

            "problem": task.get("problem") or item.get("problem") or {},
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
    _require_current_workflow(run_id)
    actor = "bearer-token" if authorization else "local-user"
    _audit_control(run_id, task_id, "approval." + request.decision, actor, request.reason)
    return _submit_approval(
        run_id,
        task_id,
        ApprovalDecision(
            decision=request.decision.upper(),
            reason=request.reason,
            retry_target=request.retry_target or "unified_fixer_agent",
        ),
        background=background,
    )

