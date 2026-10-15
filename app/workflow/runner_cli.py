"""Run one workflow step or agent from persisted production-shaped records."""

from __future__ import annotations

import argparse
import json
from typing import Any

from app.infrastructure.records.store import RecordStore
from app.infrastructure.runtime import close_sessions
from app.application.services import WorkflowServices
from app.workflow.spec import AgentSpec, StepSpec


def _store() -> RecordStore:
    from app.infrastructure.config import load_config

    return RecordStore(load_config().runtime_dir)


def require_predecessors(store: RecordStore, run_id: str, spec: StepSpec | AgentSpec, task_id: str) -> None:
    missing = []
    for dependency in spec.depends_on:
        dependency_task = task_id if dependency.level == "task" else ""
        record = store.load(run_id, dependency.name, dependency_task)
        if record is None or record.status != "succeeded":
            missing.append(dependency.name)
    if missing:
        raise SystemExit(f"{spec.name} 缺少已通过的前置步骤: {', '.join(missing)}")


def state_from_records(store: RecordStore, run_id: str, task_id: str = "") -> dict[str, Any]:
    """Rebuild graph state exclusively from this run's persisted records."""
    from app.infrastructure.config import load_config

    config = load_config()
    state: dict[str, Any] = {
        "run_id": run_id,
        "task_id": task_id,
        "attempt": 1,
        "schema_version": 2,
        "source_repo_path": str(store.load_run(run_id).get("source_repo_path") or ""),
    }
    state.update({
        "log_service_name": config.log_service_name,
        "log_service_prefix": config.log_service_prefix,
        "log_excluded_messages": list(config.ignore_errors),
        "log_excluded_biztypes": list(config.excluded_biztypes),
        "log_days": config.log_days,
        "log_limit": config.limit,
    })
    for record in store.list_steps(run_id):
        output = record.output
        if record.name == "identify_carrier":
            state["tasks"] = list(output.get("tasks") or [])
    if not task_id:
        return state

    for record in store.list_steps(run_id, task_id):
        output = record.output
        if record.name == "prepare_workspace" and record.status == "succeeded":
            state["ai_workspace_path"] = str(output.get("ai_workspace_path") or "")
        elif record.name == "prepare_evidence" and record.status == "succeeded":
            state["evidence"] = dict(output)
        elif record.name == "prepare_source_snapshot" and record.status == "succeeded":
            manifest = _manifest(store, run_id, task_id, record)
            state["baseline_manifest"] = {str(key): str(value) for key, value in manifest.items()}
            state["baseline_dir"] = str(output.get("baseline_dir") or "")
            state["source_project_path"] = str(output.get("source_project_path") or "")
            state["framework"] = str(output.get("framework") or "")
        elif record.name == "repair_agent":
            state["repair"] = output
            state["changed_files"] = list(output.get("changed_files") or [])
            state["change_plan"] = dict(output.get("change_plan") or {})
            state["attempt"] = int(record.attempt or state["attempt"])
        elif record.name == "locate_agent":
            state["locate"] = output
        elif record.name == "check_changes":
            state["check_passed"] = bool(output.get("passed"))
            state["changed_files"] = list(output.get("changed_files") or state.get("changed_files") or [])
        elif record.name == "verify_issue":
            state["verify_passed"] = bool(output.get("passed"))
            state["verify_result"] = output
        elif record.name == "run_regression":
            state["regression_passed"] = bool(output.get("passed"))
            state["regression_result"] = output
        elif record.name == "review_agent":
            state["review"] = output

    task = next((item for item in state.get("tasks", []) if item.get("task_id") == task_id), None)
    if task is None:
        raise SystemExit(f"执行 {run_id} 里没有任务 {task_id}，先完成 identify_carrier")
    state["task"] = task
    problem = task.get("problem")
    if not isinstance(problem, dict):
        raise SystemExit(f"任务 {task_id} 缺少原始 problem")
    state["problem"] = dict(problem)
    return state


def _manifest(store: RecordStore, run_id: str, task_id: str, record: Any) -> dict[str, Any]:
    """按引用取源码基线清单；旧记录没有引用，退回它自己的路径字段。"""
    refs = [ref for ref in record.artifacts if ref.name == "source_snapshot"]
    if refs:
        manifest = store.read_ref(run_id, refs[0], task_id)
    else:
        relative = str(record.output.get("manifest_artifact") or "")
        if not relative:
            raise SystemExit(f"任务 {task_id} 的 prepare_source_snapshot 记录没有基线清单引用")
        manifest = store.read_artifact(run_id, relative, task_id)
    if not isinstance(manifest, dict):
        raise SystemExit(f"任务 {task_id} 的 source_snapshot.json 必须是哈希对象")
    return manifest


def _require_identity(spec: StepSpec | AgentSpec, run_id: str, task_id: str) -> None:
    if not run_id:
        raise SystemExit("必须指定 --run-id，并使用该执行的真实 steps.json 数据")
    if spec.task_level and not task_id:
        raise SystemExit("任务级插件必须指定 --task-id")


def _require_write_confirmation(spec: StepSpec | AgentSpec, confirmed: bool) -> None:
    if spec.requires_confirmation and not confirmed:
        raise SystemExit(f"{spec.name} 会产生外部写入；确认后使用 --confirm-write")


def _finish(name: str, node: Any, store: RecordStore, state: dict[str, Any], task_id: str) -> int:
    import sys

    result = node(state)
    record = store.load(str(state["run_id"]), name, task_id)
    text = json.dumps({"result": result, "step": record.public() if record else {}}, ensure_ascii=False, indent=2)
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
        sys.stdout.buffer.write(b"\n")
    if result.get("last_result") != "passed":
        return 1
    return 0


def debug_step(
    spec: StepSpec,
    *,
    run_id: str,
    task_id: str = "",
    services: WorkflowServices | None = None,
    confirm_write: bool = False,
) -> int:
    _require_identity(spec, run_id, task_id)
    _require_write_confirmation(spec, confirm_write)
    from app.workflow.runner import bind
    from app.workflow.wiring import build_services

    store = _store()
    require_predecessors(store, run_id, spec, task_id)
    state = state_from_records(store, run_id, task_id)
    if spec.name == "discover_logs":
        state["run_id"] = run_id
    node = bind(spec, store, services or build_services())
    try:
        return _finish(spec.name, node, store, state, task_id if spec.task_level else "")
    finally:
        close_sessions()


def debug_agent(spec: AgentSpec, *, run_id: str, task_id: str, confirm_write: bool = False) -> int:
    _require_identity(spec, run_id, task_id)
    _require_write_confirmation(spec, confirm_write)
    from app.workflow.wiring import build_agent_node

    store = _store()
    require_predecessors(store, run_id, spec, task_id)
    state = state_from_records(store, run_id, task_id)
    if spec.kind == "repair":
        evidence = dict(state.get("evidence") or {})
        if not evidence.get("carrier_dir") or not evidence.get("candidates"):
            raise SystemExit("prepare_evidence 未通过或缺少候选文件，不能进入修复")
        if not state.get("baseline_manifest") or not state.get("baseline_dir"):
            raise SystemExit("缺少 prepare_source_snapshot 基线，不能进入修复")
    node = build_agent_node(spec, store)
    try:
        return _finish(spec.name, node, store, state, task_id)
    finally:
        close_sessions()


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
