"""Retry report archival shared by Run, Task and single-step execution paths."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from typing import Any


def _move_to_history(source: Path, history_dir: Path, *, label: str) -> str | None:
    if not source.is_file():
        return None
    history_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = history_dir / f"{label}-{stamp}-{uuid4().hex[:8]}{source.suffix}"
    source.replace(target)
    return str(target)


def archive_retry_reports(store: Any, run_id: str, task_id: str | None = None) -> list[str]:
    """清空本轮当前报告视图，同时保留可审计历史和失败恢复所需的 Run 备份。

    Run final.json/run_failure.json 固定归档到 *.retry_previous，供后台恢复失败
    时重建失败报告；如果已有旧备份，先移入 reports/history，避免覆盖或丢失。
    Task 报告只进入各自 history，不参与 Run 失败兜底。
    """
    run_dir = store.run_dir(run_id)
    reports = run_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    history = reports / "history"
    archived: list[str] = []

    for name in ("run_failure.json", "final.json"):
        source = reports / name
        retained = reports / f"{name}.retry_previous"
        if retained.is_file():
            previous = _move_to_history(
                retained, history, label=f"previous-{name.replace('.', '-')}",
            )
            if previous:
                archived.append(previous)
        if source.is_file():
            source.replace(retained)
            archived.append(str(retained))

    if task_id:
        task_reports = [store.task_dir(run_id, task_id) / "reports" / "final.json"]
    else:
        tasks_root = run_dir / "tasks"
        task_reports = list(tasks_root.glob("*/reports/final.json")) if tasks_root.is_dir() else []
    for source in task_reports:
        task_history = source.parent / "history"
        previous = _move_to_history(source, task_history, label="final")
        if previous:
            archived.append(previous)
    return archived


__all__ = ["archive_retry_reports"]
