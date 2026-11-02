"""发布后的独立人工审批；不进入执行图，不执行 Git 合并或删除分支。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def register_approval(store: Any, run_id: str, task_id: str, delivery: dict[str, Any]) -> dict[str, Any]:
    if not delivery.get("commit") or not delivery.get("candidate_id"):
        raise ValueError("最终审批必须绑定已提交候选和 commit")
    database = store.database(run_id)

    def transform(previous: dict[str, Any]) -> dict[str, Any]:
        if previous.get("candidate_id") == delivery["candidate_id"] and previous.get("commit") == delivery["commit"]:
            return previous
        return {"run_id": run_id, "task_id": task_id, "status": "pending", "candidate_id": delivery["candidate_id"],
                "commit": delivery["commit"], "branch": delivery.get("repair_branch", ""),
                "created_at": datetime.now(timezone.utc).isoformat(), "manual_merge": True,
                "history": [*previous.get("history", []), {key: value for key, value in previous.items() if key != "history"}] if previous else []}

    lease = store._worker_lease_for(run_id)
    if lease:
        return database.update_with_worker_lease(task_id, "final_approval", owner_id=lease[0], lease_token=lease[1], transform=transform)
    return database.update(task_id, "final_approval", transform)


def decide_approval(store: Any, run_id: str, task_id: str, *, candidate_id: str, commit: str,
                    decision: str, reason: str = "", retry_target: str = "unified_fixer_agent",
                    reason_code: str = "other") -> dict[str, Any]:
    if decision not in {"approve", "reject", "close"}:
        raise ValueError("审批决定只接受 approve/reject/close")
    if decision in {"reject", "close"} and not reason.strip():
        raise ValueError("驳回或关闭必须说明原因")

    def transform(current: dict[str, Any]) -> dict[str, Any]:
        if not current or current.get("candidate_id") != candidate_id or current.get("commit") != commit:
            raise ValueError("审批候选已变化，请重新读取当前代码及差异")
        expected = {"approve": "approved", "reject": "rejected", "close": "closed"}[decision]
        if current.get("status") == expected:
            return current
        if current.get("status") != "pending":
            raise ValueError("审批已完成，不能覆盖原决定")
        return {**current, "status": expected, "reason": reason.strip()[:2000],
                "reason_code": reason_code,
                "retry_target": retry_target if decision == "reject" else "",
                "retry_status": "pending" if decision == "reject" else "none",
                "decided_at": datetime.now(timezone.utc).isoformat()}

    return store.database(run_id).update(task_id, "final_approval", transform)
