"""12 任务收尾：只留异常、结果、状态、耗时、token 和完成步骤。"""

from __future__ import annotations

import json
from typing import Any

from app.application.audit import StepRecord
from app.application.context import StepContext
from app.workflow.steps.finalize_execution.models import FinalizeInput, FinalizeOutput

FINAL_REPORT = "reports/final.json"
_SELF = "finalize_execution"

# 阻断交付的业务结论。执行成功但结论是这几个的步骤不能算通过。
_FAILED_RESULTS = frozenset({"FAIL", "FAILED", "MANUAL", "REJECTED", "INSUFFICIENT_EVIDENCE"})


def run(ctx: StepContext, data: FinalizeInput) -> FinalizeOutput:
    """写精简 final.json。步骤全文在 steps.json，这里不再复制。"""
    records = [record for record in ctx.store.list_steps(ctx.run.run_id, data.task_id) if record.name != _SELF]
    result = _delivery_status(records, data.terminal_status)
    report = {
        "task_id": data.task_id,
        "carrier": data.carrier,
        "biztype": data.biztype,
        "problem": _problem(ctx, data.task_id),
        "result": result,
        "status": result,
        "elapsed_ms": round(sum(record.duration_ms for record in records), 2),
        "tokens": _tokens(ctx, data.task_id),
        "completed_steps": [record.name for record in records if record.status in {"succeeded", "skipped"}],
    }
    path = ctx.store.write_artifact(ctx.run.run_id, FINAL_REPORT, report, data.task_id)
    return FinalizeOutput(report_path=path, delivery_status=result)


def _problem(ctx: StepContext, task_id: str) -> str:
    tasks = ctx.store.load(ctx.run.run_id, "identify_carrier")
    items = tasks.output.get("tasks") if tasks is not None else None
    if not isinstance(items, list):
        return ""
    task = next((item for item in items if isinstance(item, dict) and item.get("task_id") == task_id), None)
    problem = task.get("problem") if isinstance(task, dict) else None
    if not isinstance(problem, dict):
        return ""
    return str(problem.get("error") or "")


def _tokens(ctx: StepContext, task_id: str) -> int:
    path = ctx.store.task_dir(ctx.run.run_id, task_id) / "metrics" / "agent.jsonl"
    if not path.is_file():
        return 0
    total = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            total += int(item.get("total_tokens") or 0)
    return total


def _delivery_status(records: list[StepRecord], terminal_status: str) -> str:
    # MANUAL 是业务终态，不是节点异常。定位/审查给出 MANUAL 时，节点本身可能是
    # succeeded；必须先检查业务结论，否则 final.json 会把转人工错误记成 FAILED。
    results = {str(record.result or "").upper() for record in records}
    if str(terminal_status or "").upper() == "CANCELLED":
        return "CANCELLED"
    if (
        _legacy_only(records)
        or str(terminal_status or "").upper() == "MANUAL"
        or bool(results & {"MANUAL", "INSUFFICIENT_EVIDENCE"})
    ):
        return "MANUAL"
    # 业务失败可能记录为 succeeded + FAIL；不能只看节点执行状态判断成功。
    if records and all(
        record.status in {"succeeded", "skipped"}
        and str(record.result or "").upper() not in _FAILED_RESULTS
        for record in records
    ):
        return "SUCCESS"
    return "FAILED"


def _legacy_only(records: list[StepRecord]) -> bool:
    return any(
        record.name == "prepare_evidence" and bool(record.output.get("legacy_only"))
        for record in records
    )


__all__ = ["FINAL_REPORT", "run"]
