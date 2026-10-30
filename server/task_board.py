"""任务板互斥结果分类与分页投影。"""

from typing import Any
from core.task_outcome import task_outcome

CATEGORIES = ("pending", "active", "paused", "changed_delivered", "unchanged_delivered", "skipped", "failed")


def task_category(task: dict[str, Any]) -> str:
    """任务类别与任务详情使用同一终态归约，不把审查 MANUAL 当任务状态。"""
    outcome = task_outcome(task)
    return {"PENDING": "pending", "RUNNING": "active"}.get(outcome, outcome.lower())


def paginate_tasks(tasks: list[dict[str, Any]], page: int, page_size: int,
                   category: str = "all", keyword: str = "") -> dict[str, Any]:
    """筛选后分页；分类计数始终涵盖整个批次，页面收缩时夹紧页码。"""
    counts = {key: 0 for key in CATEGORIES}
    for task in tasks:
        task["category"] = task_category(task)
        task["result"] = task_outcome(task)
        counts[task["category"]] += 1
    needle = keyword.strip().casefold()
    filtered = [task for task in tasks if (category == "all" or task["category"] == category)
                and (not needle or needle in " ".join(str(task.get(key) or "") for key in
                    ("carrier", "biztype", "task_id", "terminal_reason", "next_action")).casefold())]
    page = min(max(1, page), max(1, (len(filtered) + page_size - 1) // page_size))
    start = (page - 1) * page_size
    return {
        "tasks": filtered[start:start + page_size],
        "total": len(filtered),
        "all_total": len(tasks),
        "counts": counts,
        "page": page,
        "page_size": page_size,
    }


__all__ = ["CATEGORIES", "task_category", "paginate_tasks"]
