"""Run one workflow step or agent from persisted production-shaped records.

执行逻辑已经下沉到 :mod:`app.application.step_runner`，本模块只保留 CLI 外壳：
参数解析、退出码和结果打印。CLI 与 HTTP 单步执行因此共用同一套校验与状态重建。
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from app.application.step_runner import (
    StepRunnerError,
    default_store,
    dependencies_missing,
    execute_once,
    state_from_records,
)
from app.infrastructure.records.store import RecordStore
from app.workflow.spec import AgentSpec, StepSpec


def _store() -> RecordStore:
    return default_store()


def require_predecessors(store: RecordStore, run_id: str, spec: StepSpec | AgentSpec, task_id: str) -> None:
    missing = dependencies_missing(store, run_id, spec, task_id)
    if missing:
        raise SystemExit(f"{spec.name} 缺少已通过的前置步骤: {', '.join(missing)}")


def _print_result(result: dict[str, Any], record: Any) -> None:
    import sys

    text = json.dumps(
        {"result": result, "step": record.public() if record else {}},
        ensure_ascii=False,
        indent=2,
    )
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
        sys.stdout.buffer.write(b"\n")


def debug_step(
    spec: StepSpec,
    *,
    run_id: str,
    task_id: str = "",
    services: Any | None = None,
    confirm_write: bool = False,
) -> int:
    try:
        code, record, result = execute_once(
            spec,
            run_id=run_id,
            task_id=task_id,
            confirm_write=confirm_write,
            services=services,
        )
    except StepRunnerError as exc:
        raise SystemExit(str(exc)) from exc
    _print_result(result, record)
    return code


def debug_agent(spec: AgentSpec, *, run_id: str, task_id: str, confirm_write: bool = False) -> int:
    try:
        code, record, result = execute_once(
            spec,
            run_id=run_id,
            task_id=task_id,
            confirm_write=confirm_write,
        )
    except StepRunnerError as exc:
        raise SystemExit(str(exc)) from exc
    _print_result(result, record)
    return code


def _args(spec: StepSpec | AgentSpec) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"基于真实运行记录单步执行 {spec.name}")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--task-id", default="", help="任务级插件必填")
    if spec.requires_confirmation:
        parser.add_argument("--confirm-write", action="store_true", help="确认执行此步骤的外部写入")
    return parser.parse_args()


def main(spec: StepSpec) -> None:
    args = _args(spec)
    raise SystemExit(
        debug_step(
            spec,
            run_id=args.run_id,
            task_id=args.task_id,
            confirm_write=bool(getattr(args, "confirm_write", False)),
        )
    )


def main_agent(spec: AgentSpec) -> None:
    args = _args(spec)
    raise SystemExit(
        debug_agent(
            spec,
            run_id=args.run_id,
            task_id=args.task_id,
            confirm_write=bool(getattr(args, "confirm_write", False)),
        )
    )


__all__ = ["debug_agent", "debug_step", "main", "main_agent", "require_predecessors", "state_from_records"]
