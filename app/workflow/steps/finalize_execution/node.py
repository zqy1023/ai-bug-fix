"""12 任务收尾：只留异常、结果、状态、耗时、token 和完成步骤。"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from app.application.audit import StepRecord
from app.application.context import StepContext
from app.workflow.steps.finalize_execution.models import FinalizeInput, FinalizeOutput
from app.infrastructure.run_control import cancellation_requested
from app.infrastructure.config import load_config

logger = logging.getLogger(__name__)

FINAL_REPORT = "reports/final.json"
_SELF = "finalize_execution"

# 阻断交付的业务结论。执行成功但结论是这几个的步骤不能算通过。
_FAILED_RESULTS = frozenset({"FAIL", "FAILED", "MANUAL", "REJECTED", "INSUFFICIENT_EVIDENCE"})


def run(ctx: StepContext, data: FinalizeInput) -> FinalizeOutput:
    """写精简 final.json。步骤全文在 steps.json，这里不再复制。"""
    records = [record for record in ctx.store.list_steps(ctx.run.run_id, data.task_id) if record.name != _SELF]
    terminal_status = "CANCELLED" if cancellation_requested(ctx.store.root, ctx.run.run_id) else data.terminal_status
    result = _delivery_status(records, terminal_status)
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
    knowledge_case_id = _capture_knowledge_candidate(ctx, data, records, report)
    if knowledge_case_id:
        report["knowledge_case_id"] = knowledge_case_id
    path = ctx.store.write_artifact(ctx.run.run_id, FINAL_REPORT, report, data.task_id)
    return FinalizeOutput(report_path=path, delivery_status=result)



def _capture_knowledge_candidate(
    ctx: StepContext,
    data: FinalizeInput,
    records: list[StepRecord],
    report: dict[str, Any],
) -> str:
    """自动沉淀每个已收尾任务的候选案例；不自动发布，也不阻断主流程。"""
    try:
        database = load_config().runtime_dir / "workbench-control.sqlite3"
        if not database.is_file():
            return ""
        locate = next((record.output for record in records if record.name == "locate_agent"), {})
        repair = next((record.output for record in records if record.name == "repair_agent"), {})
        regression = next((record for record in records if record.name == "run_regression"), None)
        review = next((record.output for record in records if record.name == "review_agent"), {})
        root_cause = str(locate.get("root_cause") or "").strip()
        if not root_cause:
            root_cause = "尚未形成可确认的根因结论；需结合失败记录和证据人工补充"
        facts = locate.get("facts", []) if isinstance(locate, dict) else []
        source_evidence = locate.get("source_evidence", []) if isinstance(locate, dict) else []
        evidence_gaps = locate.get("evidence_gaps", []) if isinstance(locate, dict) else []
        evidence = json.dumps(
            {"facts": facts, "source_evidence": source_evidence, "evidence_gaps": evidence_gaps},
            ensure_ascii=False,
        )[:8000]
        change_summary = str(repair.get("summary") or repair.get("analysis") or "").strip()
        changed_files = repair.get("changed_files", [])
        if isinstance(changed_files, list) and changed_files:
            change_summary += "\\n变更文件：" + ", ".join(str(item) for item in changed_files[:30])
        error_text = str(report.get("problem") or "")
        fingerprint = sha256(error_text.encode("utf-8")).hexdigest() if error_text else ""
        regression_output = regression.output if regression is not None else {}
        regression_passed = bool(
            regression is not None
            and (
                str(regression.result or "").lower() in {"passed", "succeeded"}
                or (isinstance(regression_output, dict) and regression_output.get("passed") is True)
                or (isinstance(regression_output, dict) and str(regression_output.get("status") or "").upper() in {"PASS", "PASSED", "SUCCESS", "SUCCEEDED"})
            )
        )
        review_passed = str(review.get("verdict") or review.get("status") or "").upper() == "PASS" if isinstance(review, dict) else False
        case_id = uuid.uuid4().hex
        title = f"{data.carrier or '未知运输商'} / {data.biztype or '未知业务类型'}：{root_cause[:120]}"
        connection = sqlite3.connect(str(database), timeout=2)
        try:
            connection.execute(
                """INSERT OR IGNORE INTO repair_knowledge_cases
                (case_id,title,fingerprint,carrier,biztype,root_cause,evidence,change_summary,
                 regression_passed,review_passed,status,source_run_id,source_task_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,'candidate',?,?,?,?)""",
                (
                    case_id, title[:200], fingerprint, data.carrier, data.biztype, root_cause[:4000],
                    evidence, change_summary[:8000], int(regression_passed), int(review_passed),
                    ctx.run.run_id, data.task_id, datetime.now(timezone.utc).isoformat(),
                    __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                ),
            )
            connection.commit()
            row = connection.execute(
                "SELECT case_id FROM repair_knowledge_cases WHERE source_run_id=? AND source_task_id=?",
                (ctx.run.run_id, data.task_id),
            ).fetchone()
            return str(row[0]) if row else ""
        finally:
            connection.close()
    except Exception:  # noqa: BLE001 - 知识沉淀失败不能改变修复任务的业务终态
        logger.exception("自动沉淀修复知识候选失败 run_id=%s task_id=%s", ctx.run.run_id, data.task_id)
        return ""

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
