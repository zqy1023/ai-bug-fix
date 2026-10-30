"""工作台共享投影：列表、详情与 SSE 使用同一套时间和错误语义。"""

from __future__ import annotations

from typing import Any
import json
from pathlib import Path


def task_catalog(snapshot: dict[str, Any], run_dir: Path) -> tuple[list[dict[str, Any]], int]:
    """合并识别清单与已派发记录；只做投影，不为未开始任务创建执行状态。"""
    raw_slots = snapshot.get("tasks") or []
    slots = {item["task_id"]: dict(item) for item in raw_slots
             if isinstance(item, dict) and isinstance(item.get("task_id"), str)}
    dispatched = len(slots)
    identified = (snapshot.get("identify_carrier") or {}).get("output") or {}
    path = run_dir / "workflow/outputs/identify_carrier.json"
    if path.is_file():
        try:
            formal = json.loads(path.read_text(encoding="utf-8"))
            identified = (formal.get("outcome") or {}).get("output") or identified
        except (OSError, ValueError, AttributeError):
            pass  # 正式快照写入窗口内仍可使用步骤记录中的清单。
    candidates = identified.get("tasks") or []
    if isinstance(candidates, dict):
        candidates = list(candidates.values())
    ordered: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict) or not candidate.get("task_id"):
            continue
        task_id = str(candidate["task_id"])
        ordered[task_id] = {**slots.get(task_id, {"task_id": task_id}),
                            "identity": {**candidate, "input": {"problem": candidate.get("problem") or {}}}}
    ordered.update({key: value for key, value in slots.items() if key not in ordered})
    return list(ordered.values()), dispatched


def task_counts(tasks: list[dict[str, Any]], run_steps: list[dict[str, Any]]) -> dict[str, int]:
    """互斥统计完整任务清单，未开始不计入执行中或完成进度。"""
    from server.task_board import task_category
    categories = [task_category({**task, "result": task.get("status")}) for task in tasks]
    return {
        "tasks_total": len(tasks),
        "tasks_success": categories.count("changed_delivered") + categories.count("unchanged_delivered"),
        "tasks_changed_delivered": categories.count("changed_delivered"),
        "tasks_unchanged_delivered": categories.count("unchanged_delivered"),
        "tasks_failed": categories.count("failed"), "tasks_manual": 0,
        "tasks_blocked": 0, "tasks_waiting_approval": 0,
        "tasks_pending": categories.count("pending"), "tasks_running": categories.count("active"),
        "tasks_skipped": categories.count("skipped"), "tasks_cancelled": 0,
        "tasks_inactive": categories.count("skipped"),
        "tasks_completed": sum(categories.count(key) for key in ("changed_delivered", "unchanged_delivered", "failed", "skipped")),
        "steps_total": len(run_steps) + sum(len(task.get("steps") or []) for task in tasks),
    }


def run_error(failure: dict[str, Any], lifecycle: dict[str, Any]) -> dict[str, Any]:
    """优先使用正式失败报告；文件缺失时保留生命周期中的诊断。

    失败报告只在运行确实处于失败终态时展示：运行已成功、取消或正在重试恢复
    （lifecycle 已转为活动状态）时，旧失败文件不能继续当成当前错误返回，
    否则重试成功的批次仍会显示第一次的报错。
    """
    status = str(lifecycle.get("status") or "").lower()
    if status not in {"", "failed", "partial_failed"}:
        return {}
    if failure:
        return failure
    if status != "failed":
        return {}
    return {
        "status": "FAILED", "error_type": lifecycle.get("error_type"),
        "message": lifecycle.get("error_message") or lifecycle.get("terminal_reason"),
        "failed_at": lifecycle.get("finished_at"),
        "terminal_step": lifecycle.get("phase"),
        "terminal_reason": lifecycle.get("terminal_reason"),
        "next_action": lifecycle.get("next_action"),
    }


def run_timestamps(
    lifecycle: dict[str, Any], report: dict[str, Any], failure: dict[str, Any],
    *, created_at: str, terminal: bool,
) -> dict[str, Any]:
    """活动周期不继承旧终态时间；终态时间由当前生命周期及正式报告提供。"""
    return {
        "started_at": lifecycle.get("started_at") or created_at,
        "finished_at": (
            lifecycle.get("finished_at") or report.get("finished_at") or failure.get("failed_at")
        ) if terminal else None,
    }


def task_stages() -> list[dict[str, Any]]:
    """从实际图节点和插件注册表输出前端阶段契约。"""
    from workflow.engine.definitions import PHASE_COMPOSITIONS, TASK_NODE_NAMES, PUBLIC_STAGE_TITLES
    from workflow.engine.loader import load_all
    from workflow.engine.registry import resolve

    load_all()

    def title_for(name: str) -> str:
        if name in PUBLIC_STAGE_TITLES:
            return PUBLIC_STAGE_TITLES[name]
        try:
            return resolve(name).title
        except Exception:
            return name

    return [
        {
            "key": name,
            "title": title_for(name),
            "members": list(PHASE_COMPOSITIONS.get(name, (name,))),
            "group": name in PHASE_COMPOSITIONS,
        }
        for name in TASK_NODE_NAMES if name != "retry_prepare"
    ]
