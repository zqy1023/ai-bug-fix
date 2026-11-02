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
    with store._write_guard(run_id):
        return _archive_retry_reports(store, run_id, task_id)


def _archive_retry_reports(store: Any, run_id: str, task_id: str | None = None) -> list[str]:
    """先持久化文件移动计划；失败补偿，进程中断后下次调用恢复未完成计划。"""
    import json
    from infrastructure.storage.atomic import atomic_write_json

    root = store.run_dir(run_id).resolve()
    journal_root = root / "retry" / "archives"
    journal_root.mkdir(parents=True, exist_ok=True)

    def rollback(moves):
        for source_text, target_text in reversed(moves):
            source, target = Path(source_text).resolve(), Path(target_text).resolve()
            if not source.is_relative_to(root) or not target.is_relative_to(root):
                raise ValueError("报告归档计划路径越界")
            if target.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                target.replace(source)

    for journal in journal_root.glob("*.json"):
        pending = json.loads(journal.read_text(encoding="utf-8"))
        if pending.get("phase") == "preparing":
            rollback(pending["moves"])
            atomic_write_json(journal, {**pending, "phase": "rolled_back"})

    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    moves = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for name in ("run_failure.json", "final.json"):
        source, retained = reports / name, reports / f"{name}.retry_previous"
        if retained.is_file():
            target = reports / "history" / f"previous-{name.replace('.', '-')}-{stamp}-{uuid4().hex[:8]}{retained.suffix}"
            moves.append((str(retained), str(target)))
        if source.is_file():
            moves.append((str(source), str(retained)))
    tasks = ([store.task_dir(run_id, task_id) / "reports/final.json"] if task_id else
             list((root / "tasks").glob("*/reports/final.json")))
    for source in tasks:
        if source.is_file():
            moves.append((str(source), str(source.parent / "history" / f"final-{stamp}-{uuid4().hex[:8]}.json")))
    journal = journal_root / f"{uuid4().hex}.json"
    plan = {"phase": "preparing", "moves": moves}
    atomic_write_json(journal, plan)
    try:
        for source_text, target_text in moves:
            source, target = Path(source_text), Path(target_text)
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)
        atomic_write_json(journal, {**plan, "phase": "completed"})
    except BaseException:
        rollback(moves)
        atomic_write_json(journal, {**plan, "phase": "rolled_back"})
        raise
    return [target for _, target in moves]


__all__ = ["archive_retry_reports"]
