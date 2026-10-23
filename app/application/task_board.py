"""任务板互斥结果分类与分页投影。"""

from typing import Any

CATEGORIES = ("active", "approval", "success", "manual", "blocked", "failed", "skipped", "cancelled", "paused", "other")


def task_category(task: dict[str, Any]) -> str:
    """使用当前生命周期分类，审批优先于历史终态。"""
    status = str((task.get("lifecycle") or {}).get("status") or task.get("result") or "PENDING").upper()
    if task.get("approval_pending") or status == "WAITING_APPROVAL":
        return "approval"
    mapping = {"active": {"RUNNING", "RETRYING", "PENDING", "QUEUED"},
               "success": {"SUCCESS", "SUCCEEDED", "PASSED", "APPROVED"},
               "manual": {"MANUAL", "INSUFFICIENT_EVIDENCE", "UNVERIFIED"},
               "blocked": {"BLOCKED"},
               "failed": {"FAILED", "FAIL", "REJECTED", "PARTIAL_FAILED"},
               "skipped": {"SKIPPED"}, "cancelled": {"CANCELLED", "CANCELED"}, "paused": {"PAUSED"}}
    return next((key for key, values in mapping.items() if status in values), "other")


def paginate_tasks(tasks: list[dict[str, Any]], page: int, page_size: int,
                   category: str = "all", keyword: str = "") -> dict[str, Any]:
    """筛选后分页；分类计数始终涵盖整个批次，页面收缩时夹紧页码。"""
    counts = {key: 0 for key in CATEGORIES}
    for task in tasks:
        task["category"] = task_category(task)
        counts[task["category"]] += 1
    needle = keyword.strip().casefold()
    filtered = [task for task in tasks if (category == "all" or task["category"] == category)
                and (not needle or needle in " ".join(str(task.get(key) or "") for key in
                    ("carrier", "biztype", "task_id", "terminal_reason", "next_action")).casefold())]
    # 同分类保留原始任务顺序，避免轮询时行不断跳动。
    page = min(max(1, page), max(1, (len(filtered) + page_size - 1) // page_size))
    start = (page - 1) * page_size
    return {"tasks": filtered[start:start + page_size], "total": len(filtered), "all_total": len(tasks),
            "counts": counts, "page": page, "page_size": page_size}
