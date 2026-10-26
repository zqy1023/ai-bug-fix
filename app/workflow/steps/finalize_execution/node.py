"""任务收尾：只留异常、结果、状态、耗时、token 和完成步骤。"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.application.audit import StepRecord
from app.application.context import StepContext
from app.infrastructure.run_control import cancellation_requested
from app.workflow.steps.claim_repair_slot.node import release_claim
from app.workflow.steps.finalize_execution.models import FinalizeInput, FinalizeOutput

FINAL_REPORT = "reports/final.json"
logger = logging.getLogger(__name__)
_SELF = "finalize_execution"

# 阻断交付的业务结论。执行成功但结论是这几个的步骤不能算通过。
_FAILED_RESULTS = frozenset(
    {"FAIL", "FAILED", "MANUAL", "REJECTED", "INSUFFICIENT_EVIDENCE"}
)


def build_report(ctx: StepContext, data: FinalizeInput) -> dict[str, Any]:
    """Build the report from recorded evidence without finalizing the task."""
    records = [
        record
        for record in ctx.store.list_steps(ctx.run.run_id, data.task_id)
        if record.name != _SELF
    ]
    from app.workflow.contracts import Handoff

    for index, record in enumerate(records):
        artifact = (
            ctx.store.task_dir(ctx.run.run_id, data.task_id)
            / f"workflow/outputs/{record.name}.json"
        )
        if artifact.is_file():
            full = Handoff.model_validate(
                ctx.store.read_artifact(
                    ctx.run.run_id, f"workflow/outputs/{record.name}.json", data.task_id
                )
            )
            records[index] = record.model_copy(update={"output": full.outcome.output})
    terminal_status = (
        "CANCELLED"
        if cancellation_requested(ctx.store.root, ctx.run.run_id)
        else data.terminal_status
    )
    result = _delivery_status(records, terminal_status)
    terminal_step, terminal_reason, next_action = _terminal_details(
        records,
        terminal_status,
        result,
        retry_reason=data.retry_reason,
        retry_target=data.retry_target,
        blocked_reason=data.blocked_reason,
    )
    finished_at = datetime.now(timezone.utc).isoformat()
    locate = full_output(
        ctx.store, ctx.run.run_id, data.task_id, records, "locate_agent"
    )
    repair = full_output(
        ctx.store, ctx.run.run_id, data.task_id, records, "repair_agent"
    )
    from app.workflow.confirmation import confirmation

    review = full_output(
        ctx.store, ctx.run.run_id, data.task_id, records, "review_agent"
    )
    delivered = _output(records, "prepare_delivery")
    validation_decision = _output(records, "decide_validation")
    manual_required, manual_reasons = confirmation(
        {
            "validation_decision": validation_decision,
            "review": review,
            "verify_result": _output(records, "verify_issue"),
            "regression_result": _output(records, "run_regression"),
            "manual_confirmation_required": delivered.get(
                "manual_confirmation_required"
            ),
            "manual_confirmation_reasons": delivered.get("manual_confirmation_reasons")
            or [],
        }
    )
    report = {
        "report_version": 2,
        "report_kind": "task_final",
        "task_id": data.task_id,
        "carrier": data.carrier,
        "biztype": data.biztype,
        "problem": _problem(ctx, data.task_id),
        "result": result,
        "status": result,
        "terminal_status": result,
        "terminal_step": terminal_step,
        "terminal_reason": terminal_reason,
        "next_action": next_action,
        "finished_at": finished_at,
        "elapsed_ms": round(sum(record.duration_ms for record in records), 2),
        "tokens": _tokens(ctx, data.task_id),
        "root_cause": str(locate.get("root_cause") or locate.get("analysis") or ""),
        "locate_status": locate.get("status"),
        "category": locate.get("category"),
        "recommended_scope": locate.get("recommended_scope"),
        "facts": locate.get("facts", []),
        "source_evidence": locate.get("source_evidence", []),
        "evidence_gaps": locate.get("evidence_gaps", []),
        "candidate_files": locate.get("candidate_files", []),
        "change_plan": repair.get("change_plan", {}),
        "blocked_reason": repair.get("blocked_reason", ""),
        "validation": {
            name: full_output(ctx.store, ctx.run.run_id, data.task_id, records, name)
            for name in ("verify_issue", "syntax_regression", "run_regression")
        },
        "change_summary": str(repair.get("summary") or repair.get("analysis") or ""),
        "workflow_version": 7,
        "validation_plan": full_output(ctx.store, ctx.run.run_id, data.task_id, records, "plan_validation"),
        "validation_summary": full_output(ctx.store, ctx.run.run_id, data.task_id, records, "summarize_validation"),
        "validation_decision": validation_decision,
        "review_decision": str(review.get("verdict") or ""),
        "manual_confirmation_required": manual_required,
        "manual_confirmation_reasons": manual_reasons,
        "repair_failure_kind": str(repair.get("failure_kind") or ""),
        "completed_steps": [
            record.name
            for record in records
            if record.status in {"succeeded", "skipped"}
        ],
        "delivery": _output(records, "publish_delivery")
        or _output(records, "prepare_delivery"),
        "repair_branch": str(
            _output(records, "prepare_delivery").get("repair_branch")
            or _output(records, "repair_branch_preflight").get("repair_branch")
            or ""
        ),
    }
    from app.workflow.contracts import TaskResolution

    resolution = {
        "SUCCESS": "delivered",
        "MANUAL": "analysis_delivered",
        "SKIPPED": "skipped",
        "BLOCKED": "blocked",
        "REJECTED": "rejected",
        "CANCELLED": "cancelled",
    }.get(result, "failed")
    if resolution == "analysis_delivered" and not any(
        r.name == "prepare_delivery" and r.status == "succeeded" for r in records
    ):
        resolution = "blocked"
    report["resolution"] = TaskResolution(
        result=resolution,
        reason=terminal_reason,
        terminal_step=terminal_step,
        next_action=next_action,
    ).model_dump(mode="json")
    return report


def run(ctx: StepContext, data: FinalizeInput) -> FinalizeOutput:
    """写入可直接用于处置的终态报告；原始步骤记录仍以 steps.json 为准。"""
    report = build_report(ctx, data)
    result = report["result"]
    terminal_step, terminal_reason, next_action = (
        report["terminal_step"],
        report["terminal_reason"],
        report["next_action"],
    )
    finished_at = report["finished_at"]
    path = ctx.store.write_artifact(ctx.run.run_id, FINAL_REPORT, report, data.task_id)
    if data.ai_workspace_path and data.report_destination:
        workspace = Path(data.ai_workspace_path).resolve()
        if workspace.is_dir():
            report_path = (workspace / data.report_destination).resolve()
            if report_path.is_relative_to(workspace):
                try:
                    report_path.parent.mkdir(parents=True, exist_ok=True)
                    report_path.write_text(
                        json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except OSError:
                    # 运行记录中的报告已经落盘；隔离区报告无法写入时，后续报告交付节点会明确失败。
                    logger.warning(
                        "写入工作区报告副本失败 run_id=%s task_id=%s path=%s",
                        ctx.run.run_id,
                        data.task_id,
                        report_path,
                        exc_info=True,
                    )
    try:
        release_claim(ctx.store.root, run_id=ctx.run.run_id, task_id=data.task_id)
    except (OSError, sqlite3.Error):
        logger.warning(
            "释放修复资格失败 run_id=%s task_id=%s",
            ctx.run.run_id,
            data.task_id,
            exc_info=True,
        )
    return FinalizeOutput(
        resolution=report["resolution"],
        report_path=path,
        delivery_status=result,
        terminal_step=terminal_step,
        terminal_reason=terminal_reason,
        next_action=next_action,
        finished_at=finished_at,
    )


def _record_reason(record: StepRecord | None) -> str:
    if record is None:
        return ""
    error = getattr(record, "error", None)
    output = record.output if isinstance(record.output, dict) else {}
    candidates = [
        getattr(error, "message", "") if error else "",
        output.get("terminal_reason"),
        output.get("legacy_reason"),
        output.get("blocked_reason"),
        output.get("failure_reason"),
        output.get("reason"),
        output.get("summary"),
        output.get("message"),
        record.summary,
    ]
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text[:2000]
    return ""


def _terminal_details(
    records: list[StepRecord],
    terminal_status: str,
    result: str,
    *,
    retry_reason: str = "",
    retry_target: str = "",
    blocked_reason: str = "",
) -> tuple[str, str, str]:
    """从持久化步骤事实生成终态原因；不依赖模型自称成功。"""
    ordered = sorted(
        records,
        key=lambda item: str(
            getattr(item, "finished_at", "") or getattr(item, "started_at", "") or ""
        ),
    )
    latest = ordered[-1] if ordered else None
    status, outcome = str(terminal_status or "").upper(), str(result or "").upper()

    if status == "BLOCKED" or outcome == "BLOCKED":
        decision_record = next(
            (item for item in reversed(ordered) if item.name == "decide_validation"),
            None,
        )
        if decision_record and not blocked_reason:
            blocked_reason = str(decision_record.output.get("reason") or "")
        return (
            "decide_validation",
            blocked_reason or retry_reason or "验证受阻",
            "排除具体受阻原因后重新运行对应任务；无需批准未验证修改",
        )

    if status == "CANCELLED" or outcome == "CANCELLED":
        return (
            latest.name if latest else "workflow",
            "收到停止请求，任务已在安全节点边界终止。",
            "确认停止原因；需要继续时重新发起执行。",
        )

    if status == "SKIPPED" or outcome == "SKIPPED":
        skipped = next(
            (item for item in reversed(ordered) if item.status == "skipped"), latest
        )
        return (
            skipped.name if skipped else "workflow",
            _record_reason(skipped) or "任务已跳过。",
            "检查跳过原因及已有修复分支，确认是否需要重新执行。",
        )

    # 即使旧状态残留 review=MANUAL，真实的最终人工审批通过也决定了终态。
    if outcome == "SUCCESS" and _final_source_approval_accepted(records):
        return (
            "request_approval",
            "源码修复分支已提交，并通过最终人工审批。",
            "请在 Git 上检查并手动合并分支；工作流不会自动合并。",
        )

    # 成功终态短路：重试成功后，先前尝试遗留的失败记录不能把终态原因改写
    # 回旧报错；失败记录分支只在非 SUCCESS 结论下生效。
    if outcome == "SUCCESS":
        delivery = next(
            (item for item in reversed(ordered) if item.name == "prepare_delivery"),
            None,
        )
        return (
            delivery.name
            if delivery
            else (latest.name if latest else "finalize_execution"),
            _record_reason(delivery) or "任务已达到交付门槛并完成交付准备。",
            "检查交付分支和最终变更清单，按团队流程完成人工评审与合并。",
        )

    # 最后一个真实失败步骤是终态原因；不能让较早的 MANUAL 结论覆盖交付异常。
    failed_step = next(
        (item for item in reversed(ordered) if item.status == "failed"), None
    )
    if failed_step is not None:
        reason = _record_reason(failed_step) or f"步骤 {failed_step.name} 执行失败。"
        followups = {
            "prepare_delivery": "检查交付工作区、文件复制、Git 暂存与提交错误日志后重试交付。",
            "review_agent": "检查审查输入证据、模型原始输出和审查契约。",
            "run_regression": "检查回归报告、命令输出和运行环境。",
        }
        return (
            failed_step.name,
            reason,
            followups.get(
                failed_step.name, "检查失败节点的输入、输出和错误记录后重试。"
            ),
        )

    manual_names = (
        "locate_agent",
        "review_agent",
        "prepare_evidence",
        "repair_agent",
        "prepare_source_snapshot",
    )
    if outcome == "MANUAL" or status == "MANUAL":
        # Modification scope is a decisive refusal even when the diagnostic also
        # lists uncertainty. Do not ask for more evidence to resolve a permission boundary.
        for record in reversed(ordered):
            if record.name not in {"locate_agent", "repair_agent"}:
                continue
            output = record.output if isinstance(record.output, dict) else {}
            plan = output.get("change_plan") or {}
            if not (
                output.get("category") == "shared"
                or output.get("recommended_scope") == "project"
                or (isinstance(plan, dict) and plan.get("scope") == "project")
            ):
                continue
            targets = list(output.get("proposed_edit_files") or [])
            if not targets and isinstance(plan, dict):
                targets = [
                    item.get("path")
                    for item in plan.get("edits", [])
                    if isinstance(item, dict) and item.get("path")
                ]
            detail = (
                f"（{'、'.join(str(path) for path in targets[:8])}）" if targets else ""
            )
            return (
                record.name,
                f"修复范围超出本任务权限：需要修改当前运输商目录之外的文件{detail}。"
                "本任务只能修改当前运输商，不能自动修改共享层或其他运输商目录，已转人工处理。",
                "为涉及的共享层或对应运输商单独发起修复任务；当前任务不继续自动修改范围外文件。",
            )
        for name in manual_names:
            record = next(
                (item for item in reversed(ordered) if item.name == name), None
            )
            if record is None:
                continue
            output = record.output if isinstance(record.output, dict) else {}
            verdict = str(
                output.get("status") or output.get("verdict") or record.result or ""
            ).upper()
            if verdict in {"MANUAL", "INSUFFICIENT_EVIDENCE"} or str(
                record.result or ""
            ).upper() in {"MANUAL", "INSUFFICIENT_EVIDENCE"}:
                reason = _record_reason(record)
                gaps = output.get("evidence_gaps")
                if not reason and isinstance(gaps, list) and gaps:
                    reason = "证据缺口：" + "；".join(str(item) for item in gaps[:8])
                reason = (
                    reason
                    or f"{record.title or name} 要求人工处理，但没有写出具体原因。"
                )
                if "输出格式不完整" in reason or "输出契约" in reason:
                    return (
                        name,
                        reason,
                        "修正定位 Agent 输出格式或重新执行定位节点并继续流程；不要将格式错误当作业务证据不足。",
                    )
                return (
                    name,
                    reason,
                    "补充缺失证据并由人工确认处理范围；证据充分前不要自动修改代码。",
                )
        # 没有步骤明确给出人工结论时，用最后一步已经落盘的原因。
        # 不能换成“安全门禁”这类套话，否则真实产出会被盖掉。
        reason = _record_reason(latest)
        name = latest.name if latest else "workflow"
        if not reason:
            reason = f"{latest.title or name if latest else '流程'} 以人工处理结束，但该步骤没有写下具体原因。"
        return (name, reason, "查看该步骤的输出，按其中写明的原因处理。")

    approval = next(
        (item for item in reversed(ordered) if item.name == "request_approval"), None
    )
    approval_output = (
        approval.output if approval and isinstance(approval.output, dict) else {}
    )
    if (
        outcome == "REJECTED"
        or str(approval_output.get("decision") or "").upper() == "REJECT"
    ):
        detail = str(
            approval_output.get("reason") or approval_output.get("summary") or ""
        ).strip()
        reason = "人工审批拒绝了本次变更方案。拒绝原因：" + (
            detail or "审批记录未填写具体原因。"
        )
        return (
            "request_approval",
            reason,
            "根据拒绝意见调整方案；重新验证后再提交审批。",
        )

    failed_records = [
        item
        for item in ordered
        if item.status == "failed"
        or str(item.result or "").upper() in {"FAIL", "FAILED", "REJECTED"}
    ]
    if outcome in {"FAILED", "FAIL"} or failed_records:
        # 重试节点没有 StepRecord；有失败记录时以它为准，否则以 retry_target
        # 指向的节点为准，不能把上一条成功记录说成失败节点。
        failed = failed_records[-1] if failed_records else None
        name = (
            failed.name
            if failed is not None
            else (retry_target or (latest.name if latest else "workflow"))
        )
        reason = (
            (_record_reason(failed) if failed is not None else "")
            or str(retry_reason or "").strip()
            or str(blocked_reason or "").strip()
            or (_record_reason(latest) if failed is None else "")
            or "任务未通过自动修复门禁，现有步骤记录未提供更具体的失败原因。"
        )
        followups = {
            "prepare_source_snapshot": "检查源仓库中目标 carrier 是否仍存在（可能已删除或重命名）以及路径大小写后重试。",
            "prepare_evidence": "检查证据准备错误并补齐源码或运行证据后重试。",
            "locate_agent": "核对定位证据与目标文件范围后重新定位。",
            "claim_repair_slot": "确认本轮修复资格以及重复任务去重结果。",
            "prepare_workspace": "检查隔离工作区创建日志、磁盘权限和剩余空间。",
            "repair_agent": "依据修复输出调整方案后重试或转人工。",
            "check_changes": "检查变更门禁报告与文件范围，修正不合规改动。",
            "verify_issue": "检查问题验证报告和原始异常证据。",
            "run_regression": "检查准生产回归报告及失败命令。",
            "review_agent": "按审查报告修正风险项后重新提交审查。",
            "prepare_delivery": "检查分支创建、文件复制和交付记录。",
        }
        return (
            name,
            reason,
            followups.get(
                name,
                followups.get(
                    retry_target,
                    "检查失败节点的错误和产物，明确修复方案后重试或转人工。",
                ),
            ),
        )

    name = latest.name if latest else "workflow"
    reason = (
        _record_reason(latest)
        or f"任务以 {outcome or 'UNKNOWN'} 结论结束，步骤记录没有给出更具体原因。"
    )
    return (name, reason, "查看终态步骤的输入、输出和产物，确认原因后再继续。")


def _output(records: list[StepRecord], name: str) -> dict[str, Any]:
    record = next((item for item in records if item.name == name), None)
    return (
        record.output if record is not None and isinstance(record.output, dict) else {}
    )


def full_output(
    store: Any, run_id: str, task_id: str, records: list[StepRecord], name: str
) -> dict[str, Any]:
    """Read current formal output; running records may not have a handoff yet."""
    from app.workflow.definitions import REVIEW_EVIDENCE_STEPS
    if name in REVIEW_EVIDENCE_STEPS and not any(record.name == name for record in records):
        review = _output(records, "review_agent")
        if not any(item.get("name") == name for item in review.get("substeps") or []):
            return {}
    formal_path = f"workflow/outputs/{name}.json"
    if (store.task_dir(run_id, task_id) / formal_path).is_file():
        from app.workflow.contracts import Handoff
        return Handoff.model_validate(store.read_artifact(run_id, formal_path, task_id)).outcome.output
    return dict(_output(records, name))


def refresh_completed_report(store: Any, run_id: str, task_id: str) -> None:
    """Finalize local reporting only after delivery/task execution really ends."""
    from types import SimpleNamespace

    path = store.task_dir(run_id, task_id) / FINAL_REPORT
    if not path.is_file():
        return
    previous = store.read_artifact(run_id, FINAL_REPORT, task_id)
    context = SimpleNamespace(store=store, run=SimpleNamespace(run_id=run_id))
    report = build_report(
        context,
        FinalizeInput(
            task_id=task_id,
            carrier=str(previous.get("carrier") or ""),
            biztype=str(previous.get("biztype") or ""),
            terminal_status=str(previous.get("result") or ""),
            report_destination="",
        ),
    )
    records = store.list_steps(run_id, task_id)
    report.update(_execution_timing(store, run_id, task_id, records))
    report["completed_steps"] = [
        record.name for record in records if record.status in {"succeeded", "skipped"}
    ]
    store.write_artifact(run_id, FINAL_REPORT, report, task_id)


def _execution_timing(
    store: Any, run_id: str, task_id: str, records: list[StepRecord]
) -> dict[str, float]:
    """累计历史步骤；旧 Agent 历史从模型调用账本补齐，避免重复计算。"""
    all_records = {(r.name, r.attempt, r.started_at): r for r in records}
    for body in store.step_history(run_id, task_id):
        try:
            record = StepRecord.model_validate(body)
        except (OSError, ValueError):
            continue
        all_records.setdefault((record.name, record.attempt, record.started_at), record)
    total = sum(r.duration_ms for r in all_records.values())
    agent_totals: dict[str, float] = {}
    for row in store.read_metrics(run_id, task_id):
        try:
            name = str(row.get("agent_role") or "") + "_agent"
            agent_totals[name] = agent_totals.get(name, 0) + float(
                row.get("duration_ms") or 0
            )
        except (ValueError, TypeError):
            continue
    for name, elapsed in agent_totals.items():
        total += max(
            0,
            elapsed
            - sum(r.duration_ms for r in all_records.values() if r.name == name),
        )
    starts = [
        datetime.fromisoformat(r.started_at)
        for r in all_records.values()
        if r.started_at
    ]
    ends = [
        datetime.fromisoformat(r.finished_at)
        for r in all_records.values()
        if r.finished_at
    ]
    wall = (
        max(0, (max(ends) - min(starts)).total_seconds() * 1000)
        if starts and ends
        else 0
    )
    return {
        "elapsed_ms": round(total, 2),
        "cumulative_execution_ms": round(total, 2),
        "latest_records_ms": round(sum(r.duration_ms for r in records), 2),
        "wall_elapsed_ms": round(wall, 2),
    }


def _problem(ctx: StepContext, task_id: str) -> str:
    tasks = ctx.store.load(ctx.run.run_id, "identify_carrier")
    items = tasks.output.get("tasks") if tasks is not None else None
    if not isinstance(items, list):
        return ""
    task = next(
        (
            item
            for item in items
            if isinstance(item, dict) and item.get("task_id") == task_id
        ),
        None,
    )
    problem = task.get("problem") if isinstance(task, dict) else None
    if not isinstance(problem, dict):
        return ""
    return str(problem.get("error") or "")


def _tokens(ctx: StepContext, task_id: str) -> int | None:
    total = 0
    for item in ctx.store.read_metrics(ctx.run.run_id, task_id):
        if item.get("total_tokens") is None:
            return None
        try:
            total += max(0, int(item["total_tokens"]))
        except (TypeError, ValueError):
            return None
    return total


def _successful_delivery(records: list[StepRecord]) -> StepRecord | None:
    """最近一次成功发布记录；用于判定失败记录是否属于重试前的旧尝试。"""
    return next(
        (
            record
            for record in reversed(records)
            if record.name == "publish_delivery"
            and record.status in {"succeeded", "skipped"}
        ),
        None,
    )


def _superseded_failure(record: StepRecord, delivery: StepRecord | None) -> bool:
    """发布成功之前的失败记录属于重试前的旧尝试。

    同一尝试内失败的节点会被重试路由重跑、记录被覆盖，不会带着 failed
    记录走到发布成功；因此早于成功发布记录的 failed 记录一律视为历史，
    不参与终态判断，否则重试成功的任务会继续显示第一次的报错。
    """
    if delivery is None or record.status != "failed":
        return False
    failed_at = str(record.finished_at or "").strip()
    delivered_at = str(delivery.finished_at or "").strip()
    return bool(failed_at) and bool(delivered_at) and delivered_at > failed_at


def _delivery_status(records: list[StepRecord], terminal_status: str) -> str:
    # MANUAL 是业务终态，不是节点异常。定位/审查给出 MANUAL 时，节点本身可能是
    # succeeded；必须先检查业务结论，否则 final.json 会把转人工错误记成 FAILED。
    if str(terminal_status or "").upper() == "BLOCKED":
        return "BLOCKED"
    results = {str(record.result or "").upper() for record in records}
    if str(terminal_status or "").upper() == "CANCELLED":
        return "CANCELLED"
    # ReviewAgent 的 MANUAL 代表“已交付后由人复核”，不是最终任务状态。
    # 只有源码分支已经交付且 request_approval 明确 APPROVE，才能覆盖该中间结论。
    # 该判定必须先于失败记录检查：与 _terminal_details 的推导顺序保持一致
    # （终态明细同样先看"审批通过且发布成功"再看失败步骤），否则"审批通过+发布
    # 成功+历史失败记录（重试前的旧记录）"这个组合会被两个函数分别推导成
    # FAILED 与 SUCCESS。纯失败/纯取消场景不经过这里，输出不变。
    if _final_source_approval_accepted(records):
        return "SUCCESS"
    delivery = _successful_delivery(records)
    records = [record for record in records if not _superseded_failure(record, delivery)]
    # 执行步骤真实失败优先于“转人工”业务结论。否则 review=MANUAL 后，
    # prepare_delivery 再失败会被 MANUAL 覆盖，任务板错误地显示在分析报告列。
    failed_records = [record for record in records if record.status == "failed"]
    if failed_records:
        return "FAILED"
    analysis_delivery = next(
        (
            r
            for r in reversed(records)
            if r.name == "prepare_delivery"
            and r.status == "succeeded"
            and r.input.get("analysis_only")
        ),
        None,
    )
    if analysis_delivery and _output(records, "prepare_analysis"):
        return "MANUAL"
    if (
        str(terminal_status or "").upper() == "MANUAL"
        or bool(results & {"MANUAL", "INSUFFICIENT_EVIDENCE"})
        or any(
            record.name == "locate_agent"
            and record.output.get("status") == "LOCATED"
            and (
                record.output.get("category") != "carrier"
                or record.output.get("recommended_scope") != "carrier"
            )
            for record in records
        )
    ):
        return "MANUAL"
    if "SKIPPED" in results:
        return "SKIPPED"
    # 业务失败可能记录为 succeeded + FAIL；不能只看节点执行状态判断成功。
    if records and all(
        record.status in {"succeeded", "skipped"}
        and str(record.result or "").upper() not in _FAILED_RESULTS
        for record in records
    ):
        # 步骤都"成功"不等于交付成功：没有真实提交（远端或源仓库本地分支可查）
        # 就不能对外声称自动交付，否则界面上会出现查不到的"已提交"。
        return "SUCCESS" if _delivery_recorded(records) else "FAILED"
    return "FAILED"


def _delivery_recorded(records: list[StepRecord]) -> bool:
    """确认本轮确实产出过修复提交，并且提交落到了远端或源仓库本地分支。"""
    delivery = next(
        (record for record in reversed(records) if record.name == "publish_delivery"),
        None,
    )
    if delivery is None or delivery.status not in {"succeeded", "skipped"}:
        return False
    output = delivery.output if isinstance(delivery.output, dict) else {}
    commit = str(output.get("commit") or "").strip()
    landed = bool(output.get("pushed") or output.get("local_branch"))
    return bool(commit) and landed


def _final_source_approval_accepted(records: list[StepRecord]) -> bool:
    """区分中间 Review=MANUAL 与真正完成的最终人工审批。"""
    approval = next(
        (record for record in reversed(records) if record.name == "request_approval"),
        None,
    )
    delivery = next(
        (record for record in reversed(records) if record.name == "publish_delivery"),
        None,
    )
    if approval is None or delivery is None:
        return False
    if approval.status != "succeeded" or delivery.status not in {
        "succeeded",
        "skipped",
    }:
        return False
    approval_output = approval.output if isinstance(approval.output, dict) else {}
    delivery_input = delivery.input if isinstance(delivery.input, dict) else {}
    # 分析报告分支的提交不构成源码修复审批。
    if delivery_input.get("analysis_only") is True:
        return False
    if str(approval_output.get("decision") or "").upper() != "APPROVE":
        return False
    return _delivery_recorded(records)


__all__ = ["FINAL_REPORT", "run"]
