"""工作台共享投影：列表、详情与 SSE 使用同一套时间和错误语义。"""

from __future__ import annotations

from typing import Any


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
