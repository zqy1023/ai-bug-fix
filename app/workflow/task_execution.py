"""Run task checkpoints independently so approval waits do not suspend dispatch."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from langgraph.errors import GraphInterrupt


def task_config(run_id: str, task_id: str, recursion_limit: int = 1000) -> dict[str, Any]:
    """Return a stable, separate checkpoint identity for one task."""
    return {
        "configurable": {"thread_id": f"{run_id}:task:{task_id}"},
        "recursion_limit": recursion_limit,
    }


def invoke_task(
    graph: CompiledStateGraph, value: Any, config: dict[str, Any],
) -> dict[str, Any]:
    """Execute outside the parent's LangGraph context and interrupt namespace."""
    # A fresh thread deliberately does not inherit the parent's contextvars. Otherwise
    # LangGraph attaches this checkpoint/interrupt to the currently running parent node.
    from app.workflow.validation import assert_workflow_version
    snapshot = graph.get_state(config) if graph.checkpointer else None
    if snapshot is not None and snapshot.values:
        assert_workflow_version(snapshot.values)
    elif isinstance(value, dict):
        assert_workflow_version(value)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="workflow-task") as pool:
        return pool.submit(graph.invoke, value, config=config).result()


def task_result(state: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Return only parent channels, leaving pending tasks nonterminal."""
    if result.get("__interrupt__"):
        interrupts = result["__interrupt__"]
        if any(not isinstance(item.value, dict) or item.value.get("kind") != "change_approval"
               for item in interrupts):
            # User pause/cancel controls must still stop dispatch at the run boundary.
            raise GraphInterrupt(interrupts)
        task = dict(state.get("task") or {})
        return {
            "terminal_status": "WAITING_APPROVAL", "last_result": "waiting",
            "task_summary": {
                "task_id": str(state["task_id"]), "carrier": str(task.get("carrier") or ""),
                "biztype": str(task.get("biztype") or ""), "result": "WAITING_APPROVAL",
            },
        }
    return {key: result.get(key) for key in ("terminal_status", "last_result", "task_summary")}
