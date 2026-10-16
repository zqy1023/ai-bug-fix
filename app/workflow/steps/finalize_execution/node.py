"""12 任务收尾：只留异常、结果、状态、耗时、token 和完成步骤。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.application.audit import StepRecord
from app.application.context import StepContext
from app.workflow.steps.finalize_execution.models import FinalizeInput, FinalizeOutput
from app.infrastructure.run_control import cancellation_requested

FINAL_REPORT = "reports/final.json"
_SELF = "finalize_execution"

# 阻断交付的业务结论。执行成功但结论是这几个的步骤不能算通过。
_FAILED_RESULTS = frozenset({"FAIL", "FAILED", "MANUAL", "REJECTED", "INSUFFICIENT_EVIDENCE"})


def run(ctx: StepContext, data: FinalizeInput) -> FinalizeOutput:
    """写入可直接用于处置的终态报告；原始步骤记录仍以 steps.json 为准。"""
    records = [record for record in ctx.store.list_steps(ctx.run.run_id, data.task_id) if record.name != _SELF]
    terminal_status = "CANCELLED" if cancellation_requested(ctx.store.root, ctx.run.run_id) else data.terminal_status
    result = _delivery_status(records, terminal_status)
    terminal_step, terminal_reason, next_action = _terminal_details(records, terminal_status, result)
    finished_at = datetime.now(timezone.utc).isoformat()
    locate = _output(records, "locate_agent")
    repair = _output(records, "repair_agent")
    report = {
        "report_version": 2, "report_kind": "task_final",
        "task_id": data.task_id, "carrier": data.carrier, "biztype": data.biztype,
        "problem": _problem(ctx, data.task_id), "result": result, "status": result,
        "terminal_status": result, "terminal_step": terminal_step,
        "terminal_reason": terminal_reason, "next_action": next_action, "finished_at": finished_at,
        "elapsed_ms": round(sum(record.duration_ms for record in records), 2),
        "tokens": _tokens(ctx, data.task_id),
        "root_cause": str(locate.get("root_cause") or locate.get("analysis") or ""),
        "change_summary": str(repair.get("summary") or repair.get("analysis") or ""),
        "completed_steps": [record.name for record in records if record.status in {"succeeded", "skipped"}],
    }
    # 知识沉淀已从自动工作流中移除；终态报告只记录本次任务事实。
    path = ctx.store.write_artifact(ctx.run.run_id, FINAL_REPORT, report, data.task_id)
    if data.ai_workspace_path:
        workspace = Path(data.ai_workspace_path).resolve()
        if workspace.is_dir():
            report_path = (workspace / FINAL_REPORT).resolve()
            if report_path.is_relative_to(workspace):
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return FinalizeOutput(report_path=path, delivery_status=result, terminal_step=terminal_step,
                          terminal_reason=terminal_reason, next_action=next_action, finished_at=finished_at)


def _record_reason(record: StepRecord | None) -> str:
    if record is None:
        return ""
    error = getattr(record, "error", None)
    output = record.output if isinstance(record.output, dict) else {}
    candidates = [
        getattr(error, "message", "") if error else "",
        output.get("terminal_reason"), output.get("legacy_reason"),
        output.get("failure_reason"), output.get("reason"),
        output.get("summary"), output.get("message"), record.summary,
    ]
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text[:2000]
    return ""


def _terminal_details(
    records: list[StepRecord], terminal_status: str, result: str
) -> tuple[str, str, str]:
    """从持久化步骤事实生成终态原因；不依赖模型自称成功。"""
    ordered = sorted(records, key=lambda item: str(item.finished_at or item.started_at or ""))
    latest = ordered[-1] if ordered else None
    status, outcome = str(terminal_status or "").upper(), str(result or "").upper()

    if status == "CANCELLED" or outcome == "CANCELLED":
        return (latest.name if latest else "workflow",
                "收到停止请求，任务已在安全节点边界终止。",
                "确认停止原因；需要继续时重新发起执行。")

    evidence = next((item for item in ordered if item.name == "prepare_evidence"), None)
    evidence_output = evidence.output if evidence and isinstance(evidence.output, dict) else {}
    if evidence_output.get("legacy_only"):
        reason = str(evidence_output.get("legacy_reason") or _record_reason(evidence) or
                     "证据准备确认目标运输商只有旧框架实现，不能进入当前自动修复路径。")
        return ("prepare_evidence", reason,
                "人工核对真实采集入口和调用链，确认迁移或兼容方案后再执行修复。")

    manual_names = ("locate_agent", "review_agent", "prepare_evidence", "repair_agent")
    if outcome == "MANUAL" or status == "MANUAL":
        for name in manual_names:
            record = next((item for item in reversed(ordered) if item.name == name), None)
            if record is None:
                continue
            output = record.output if isinstance(record.output, dict) else {}
            verdict = str(output.get("status") or output.get("verdict") or record.result or "").upper()
            if verdict in {"MANUAL", "INSUFFICIENT_EVIDENCE"} or str(record.result or "").upper() in {"MANUAL", "INSUFFICIENT_EVIDENCE"}:
                reason = _record_reason(record)
                gaps = output.get("evidence_gaps")
                if not reason and isinstance(gaps, list) and gaps:
                    reason = "证据缺口：" + "；".join(str(item) for item in gaps[:8])
                reason = reason or "安全门禁要求人工介入，但步骤未写明具体原因。"
                return (name, reason,
                        "补充缺失证据并由人工确认处理范围；证据充分前不要自动修改代码。")
        return (latest.name if latest else "workflow",
                "流程因安全门禁要求人工介入而结束，现有记录未给出更具体原因。",
                "检查最后执行步骤的输出，补录人工处置原因后再继续。")

    approval = next((item for item in reversed(ordered) if item.name == "request_approval"), None)
    approval_output = approval.output if approval and isinstance(approval.output, dict) else {}
    if outcome == "REJECTED" or str(approval_output.get("decision") or "").upper() == "REJECT":
        detail = str(approval_output.get("reason") or approval_output.get("summary") or "").strip()
        reason = "人工审批拒绝了本次变更方案。拒绝原因：" + (detail or "审批记录未填写具体原因。")
        return ("request_approval", reason,
                "根据拒绝意见调整方案；重新验证后再提交审批。")

    failed_records = [
        item for item in ordered
        if item.status == "failed" or str(item.result or "").upper() in {"FAIL", "FAILED", "REJECTED"}
    ]
    if outcome in {"FAILED", "FAIL"} or failed_records:
        failed = failed_records[-1] if failed_records else latest
        name = failed.name if failed else "workflow"
        reason = _record_reason(failed) or "任务未通过自动修复门禁，现有步骤记录未提供更具体的失败原因。"
        followups = {
            "prepare_source_snapshot": "检查源仓库和目标 carrier 路径后重试。",
            "prepare_evidence": "检查证据准备错误并补齐源码或运行证据后重试。",
            "locate_agent": "核对定位证据与目标文件范围后重新定位。",
            "claim_repair_slot": "确认本轮修复资格以及重复任务去重结果。",
            "prepare_workspace": "检查隔离工作区创建日志、磁盘权限和剩余空间。",
            "repair_agent": "依据修复输出调整方案后重试或转人工。",
            "check_changes": "检查变更门禁报告与文件范围，修正不合规改动。",
            "verify_issue": "检查问题验证报告和原始异常证据。",
            "run_regression": "检查准生产回归报告及失败命令。",
            "review_agent": "按审查报告修正风险项后重新提交审查。",
            "apply_plan": "核对获批方案与实际落盘结果，再重新验证。",
            "prepare_delivery": "检查分支创建、文件复制和交付记录。",
        }
        return (name, reason, followups.get(name, "检查失败节点的错误和产物，明确修复方案后重试或转人工。"))

    if outcome == "SUCCESS":
        delivery = next((item for item in reversed(ordered) if item.name == "prepare_delivery"), None)
        return (delivery.name if delivery else (latest.name if latest else "finalize_execution"),
                _record_reason(delivery) or "任务已达到交付门槛并完成交付准备。",
                "检查交付分支和最终变更清单，按团队流程完成人工评审与合并。")

    name = latest.name if latest else "workflow"
    reason = _record_reason(latest) or f"任务以 {outcome or 'UNKNOWN'} 结论结束，步骤记录没有给出更具体原因。"
    return (name, reason, "查看终态步骤的输入、输出和产物，确认原因后再继续。")

def _output(records: list[StepRecord], name: str) -> dict[str, Any]:
    record = next((item for item in records if item.name == name), None)
    return record.output if record is not None and isinstance(record.output, dict) else {}



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
            try:
                total += max(0, int(item.get("total_tokens") or 0))
            except (TypeError, ValueError):
                continue
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
