"""服务入口。

完整执行走编译后的 LangGraph：``python -m app.entrypoint``。
单步调试不走这里，去各插件自己的 ``__main__``。
"""

from __future__ import annotations

import uuid
import json
import logging
from datetime import datetime, timezone
from dataclasses import replace
from typing import Any

from app.infrastructure.config import load_config
from app.infrastructure.records.store import RecordStore
from app.infrastructure.runtime import close_sessions

logger = logging.getLogger(__name__)


def prepare_run(
    run_id: str | None = None,
    *,
    overrides: dict[str, Any] | None = None,
    queued: bool = False,
    allow_prepared: bool = False,
) -> tuple[str, Any, RecordStore]:
    """同步创建可查询的 Run 元数据；供 API 接受请求前与后台执行共用。"""
    config = load_config()
    options = dict(overrides or {})
    config = replace(
        config,
        log_days=int(options.get("log_days", config.log_days)),
        limit=(config.limit if int(options.get("log_limit", 0)) == 0 else int(options["log_limit"])),
        review_mode=str(options.get("review_mode", config.review_mode)),
        max_attempts=int(options.get("max_attempts", config.max_attempts)),
    )
    if not 1 <= config.log_days <= 30 or not 1 <= config.max_attempts <= 5 or config.limit < 0:
        raise ValueError("执行参数超出允许范围")
    if config.review_mode not in ("agent", "human"):
        raise ValueError("review_mode 只能是 agent 或 human")
    if config.source_repo is None:
        raise RuntimeError("AI_REPAIR_SOURCE_REPO 未配置，禁止启动完整修复工作流")

    resolved = run_id or uuid.uuid4().hex
    store = RecordStore(config.runtime_dir)
    metadata = {
        "source_repo_path": str(config.source_repo),
        "project_subdir": str(config.project_subdir or ""),
        "log_service_name": config.log_service_name,
        "log_service_prefix": config.log_service_prefix,
        "log_excluded_messages": list(config.ignore_errors),
        "log_excluded_biztypes": list(config.excluded_biztypes),
        "log_days": config.log_days,
        "log_limit": config.limit,
        "review_mode": config.review_mode,
        "max_attempts": config.max_attempts,
        "startup_state": "queued" if queued else "started",
    }
    run_json = store.run_dir(resolved) / "run.json"
    if run_json.is_file():
        if not allow_prepared:
            raise RuntimeError(f"执行 {resolved} 已存在，禁止覆盖或重复启动")
        existing = store.load_run(resolved)
        if existing.get("startup_state") != "queued":
            raise RuntimeError(f"执行 {resolved} 不是待启动状态，拒绝重复启动")
        mismatches = [
            key for key, value in metadata.items()
            if key != "startup_state" and existing.get(key) != value
        ]
        if mismatches:
            raise RuntimeError(
                f"执行 {resolved} 已存在，但配置不匹配：{', '.join(mismatches)}"
            )
        existing["startup_state"] = "started"
        temporary = run_json.with_suffix(".json.tmp")
        import json

        temporary.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(run_json)
    else:
        if allow_prepared:
            raise RuntimeError(f"执行 {resolved} 缺少 API 预创建的运行记录，拒绝后台启动")
        store.create_run(resolved, metadata)
    return resolved, config, store


def run_workflow(
    run_id: str | None = None,
    *,
    overrides: dict[str, Any] | None = None,
    prepared: bool = False,
) -> dict[str, Any]:
    """执行一次完整工作流；每个 carrier 任务使用自己的隔离工作区。"""
    resolved, config, store = prepare_run(
        run_id, overrides=overrides, allow_prepared=prepared
    )
    try:
        from app.workflow.wiring import build_graph

        graph = build_graph(store=store)
        final_state = graph.invoke(
            {
                "schema_version": 2,
                "run_id": resolved,
                "source_repo_path": str(config.source_repo),
                "log_service_name": config.log_service_name,
                "log_service_prefix": config.log_service_prefix,
                "log_excluded_messages": list(config.ignore_errors),
                "log_excluded_biztypes": list(config.excluded_biztypes),
                "log_days": config.log_days,
                "log_limit": config.limit,
                "attempt": 1,
                "max_attempts": config.max_attempts,
                "review_mode": config.review_mode,
            },
            config={"configurable": {"thread_id": resolved}, "recursion_limit": 40},
        )
        _write_run_report_if_terminal(store, resolved, graph, final_state=final_state)
    except Exception as exc:
        _write_run_report(store, resolved, error=exc)
        raise
    finally:
        close_sessions()
    return {"run_id": resolved, "steps": [item.name for item in store.list_steps(resolved)]}


def _write_run_report_if_terminal(
    store: RecordStore,
    run_id: str,
    graph: Any,
    *,
    final_state: dict[str, Any] | None = None,
) -> None:
    """只有图确实进入终态时才写 Run final.json；暂停和等待审批不是终态。"""
    try:
        snapshot = graph.get_state({"configurable": {"thread_id": run_id}})
        interrupted = any(
            bool(getattr(task, "interrupts", ()))
            for task in (getattr(snapshot, "tasks", ()) or ())
        )
        if interrupted or (isinstance(final_state, dict) and final_state.get("__interrupt__")):
            return
    except Exception:
        # 无法读取 checkpoint 时，只能依赖 invoke 返回值中是否含有 interrupt 标记。
        if isinstance(final_state, dict) and final_state.get("__interrupt__"):
            return
        logger.debug("无法确认工作流 checkpoint 是否处于 interrupt 状态 run_id=%s", run_id, exc_info=True)
    _write_run_report(store, run_id, final_state=final_state)


def _task_slot_identity(slot: dict[str, Any], key: str) -> str:
    direct = slot.get(key)
    if direct:
        return str(direct)
    for value in slot.values():
        if not isinstance(value, dict):
            continue
        if value.get(key):
            return str(value[key])
        for nested_key in ("input", "output", "result"):
            nested = value.get(nested_key)
            if isinstance(nested, dict) and nested.get(key):
                return str(nested[key])
    return ""


def _ensure_task_final_report(
    store: RecordStore,
    run_id: str,
    slot: dict[str, Any],
    task_id: str,
    existing: dict[str, Any],
    *,
    finished_at: str,
    error: Exception | None,
) -> dict[str, Any]:
    """Run 终止时为每个任务补齐 final.json，包括没走到 finalize_execution 的异常路径。"""
    all_records = store.list_steps(run_id, task_id)
    records = [record for record in all_records if record.name != "finalize_execution"]
    finalizer = next((record for record in reversed(all_records) if record.name == "finalize_execution"), None)
    final_output = finalizer.output if finalizer and isinstance(finalizer.output, dict) else {}

    result = str(existing.get("result") or final_output.get("delivery_status") or "").upper()
    if result in {"COMPLETED", "SUCCEEDED"} and final_output.get("delivery_status"):
        result = str(final_output["delivery_status"]).upper()
    terminal_status = str(existing.get("terminal_status") or result).upper()

    if not result:
        if any(
            isinstance(record.output, dict) and record.output.get("legacy_only")
            for record in records
        ) or any(str(record.result or "").upper() in {"MANUAL", "INSUFFICIENT_EVIDENCE"} for record in records):
            result = "MANUAL"
        elif any(record.status == "failed" or str(record.result or "").upper() in {"FAIL", "FAILED", "REJECTED"} for record in records):
            result = "FAILED"
        elif error is not None:
            result = "FAILED"
        else:
            result = "INCOMPLETE"
        terminal_status = result

    try:
        from app.workflow.steps.finalize_execution.node import _terminal_details
        terminal_step, inferred_reason, inferred_action = _terminal_details(records, terminal_status or result, result)
    except Exception:
        logger.exception("任务终态原因推导失败 run_id=%s task_id=%s", run_id, task_id)
        terminal_step = str(records[-1].name if records else "workflow")
        inferred_reason = "任务已结束，但系统未能从持久化步骤记录还原具体结束原因。"
        inferred_action = "查看最后一个步骤的输入、输出和错误，补齐处置记录后再重试或转人工。"

    has_recorded_reason = bool(str(existing.get("terminal_reason") or final_output.get("terminal_reason") or "").strip())
    terminal_reason = str(existing.get("terminal_reason") or final_output.get("terminal_reason") or inferred_reason or "").strip()
    next_action = str(existing.get("next_action") or final_output.get("next_action") or inferred_action or "").strip()
    if error is not None and result == "FAILED" and not has_recorded_reason:
        terminal_reason = f"工作流在任务执行期间异常退出：{type(error).__name__}: {str(error).strip()}"[:2000]
        next_action = "检查服务端异常堆栈、最后步骤的输入输出和任务产物；修复异常后重新执行。"

    carrier = str(existing.get("carrier") or _task_slot_identity(slot, "carrier"))
    biztype = str(existing.get("biztype") or _task_slot_identity(slot, "biztype"))
    locate = next((record.output for record in all_records if record.name == "locate_agent" and isinstance(record.output, dict)), {})
    repair = next((record.output for record in all_records if record.name == "repair_agent" and isinstance(record.output, dict)), {})
    return {
        **existing,
        "report_version": max(int(existing.get("report_version") or 0), 2),
        "report_kind": "task_final",
        "task_id": task_id, "carrier": carrier, "biztype": biztype,
        "result": result, "status": result, "terminal_status": result,
        "terminal_step": str(existing.get("terminal_step") or final_output.get("terminal_step") or terminal_step or "workflow"),
        "terminal_reason": terminal_reason or "任务结束，但现有记录没有提供更具体的结束原因。",
        "next_action": next_action or "查看终态步骤的错误详情和关联产物，确认后续处置动作。",
        "finished_at": str(existing.get("finished_at") or final_output.get("finished_at") or finished_at),
        "elapsed_ms": existing.get("elapsed_ms") if existing.get("elapsed_ms") is not None else
            round(sum(float(record.duration_ms or 0) for record in all_records), 2),
        "completed_steps": existing.get("completed_steps") or [
            record.name for record in all_records if record.status in {"succeeded", "skipped"}
        ],
        "root_cause": str(existing.get("root_cause") or locate.get("root_cause") or locate.get("analysis") or ""),
        "change_summary": str(existing.get("change_summary") or repair.get("summary") or repair.get("analysis") or ""),
        "legacy_report": bool(existing.get("legacy_report")) or not bool(existing.get("terminal_reason")),
    }


def _write_run_report(
    store: RecordStore,
    run_id: str,
    *,
    final_state: dict[str, Any] | None = None,
    error: Exception | None = None,
) -> None:
    """每轮执行无论正常、无任务还是异常退出，都写一份 Run 级终态报告。"""
    now = datetime.now(timezone.utc).isoformat()
    try:
        run_dir = store.run_dir(run_id)
        reports_dir = run_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        steps_path = store.steps_path(run_id)
        payload = json.loads(steps_path.read_text(encoding="utf-8")) if steps_path.is_file() else {}
        slots = payload.get("tasks", []) if isinstance(payload, dict) else []
        task_results = []
        for slot in slots if isinstance(slots, list) else []:
            if not isinstance(slot, dict) or not isinstance(slot.get("task_id"), str):
                continue
            task_id = slot["task_id"]
            report_path = store.task_dir(run_id, task_id) / "reports" / "final.json"
            existing_report = _read_json(report_path)
            report = _ensure_task_final_report(
                store, run_id, slot, task_id,
                existing_report if isinstance(existing_report, dict) else {},
                finished_at=now, error=error,
            )
            report_path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_json(report_path, report)
            task_results.append({
                "task_id": task_id,
                "carrier": str(report.get("carrier") or ""),
                "biztype": str(report.get("biztype") or ""),
                "result": str(report.get("result") or "INCOMPLETE"),
                "terminal_step": str(report.get("terminal_step") or ""),
                "terminal_reason": str(report.get("terminal_reason") or "任务结束，但无法生成更具体的原因。"),
                "next_action": str(report.get("next_action") or "检查该任务最后一个步骤的输出和产物。"),
            })

        run_steps = store.list_steps(run_id)
        failed_steps = [step for step in run_steps if step.status == "failed"]
        cancellation_requested = False
        try:
            from app.infrastructure.run_control import cancellation_requested as _cancel_requested
            cancellation_requested = _cancel_requested(store.root, run_id)
        except Exception:
            logger.debug("读取 Run 取消标记失败 run_id=%s", run_id, exc_info=True)

        if error is not None:
            result = "FAILED"
            terminal = failed_steps[-1] if failed_steps else (run_steps[-1] if run_steps else None)
            reason = f"{type(error).__name__}: {error}".strip()
            terminal_step = terminal.name if terminal else "workflow"
            next_action = "检查工作流异常堆栈、最后一个节点的输入输出和运行产物后重试。"
            failure = {
                "status": "FAILED", "error_type": type(error).__name__, "message": str(error),
                "failed_at": now, "terminal_step": terminal_step, "terminal_reason": reason,
            }
            _atomic_json(reports_dir / "run_failure.json", failure)
        elif cancellation_requested:
            result = "CANCELLED"
            terminal = run_steps[-1] if run_steps else None
            terminal_step = terminal.name if terminal else "workflow"
            reason = "收到停止请求，本轮执行已按安全节点策略终止。"
            next_action = "确认停止原因；如仍需修复，请在问题和证据确认后重新发起一轮执行。"
        elif failed_steps:
            terminal = failed_steps[-1]
            result = "FAILED"
            reason = str(getattr(terminal.error, "message", "") or terminal.summary or
                         f"Run 级步骤 {terminal.name} 执行失败，但没有记录具体原因。")
            terminal_step = terminal.name
            next_action = "检查失败节点的错误详情和产物，修复配置或输入后重新执行。"
        elif not task_results:
            result = "NO_TASKS"
            terminal = next((step for step in reversed(run_steps) if step.name == "identify_carrier"),
                            run_steps[-1] if run_steps else None)
            terminal_step = terminal.name if terminal else "workflow"
            reason = "本轮工作流已结束，但没有产生可处理的运输商任务。"
            next_action = "检查日志筛选条件、异常规范化、去重结果和运输商识别输出。"
        else:
            counts = {}
            for task in task_results:
                outcome = task["result"].upper()
                counts[outcome] = counts.get(outcome, 0) + 1
            total = len(task_results)
            successes = counts.get("SUCCESS", 0) + counts.get("SUCCEEDED", 0)
            manuals = counts.get("MANUAL", 0) + counts.get("INSUFFICIENT_EVIDENCE", 0)
            failures = sum(count for key, count in counts.items()
                           if key in {"FAILED", "FAIL", "REJECTED", "INCOMPLETE", "CANCELLED"})
            if failures or manuals:
                result = "PARTIAL_FAILED" if successes else ("MANUAL" if manuals and not failures else "FAILED")
            elif successes == total:
                result = "SUCCESS"
            else:
                result = "INCOMPLETE"
            blocker = next((task for task in task_results if task["result"].upper() not in {"SUCCESS", "SUCCEEDED"}), None)
            terminal_step = str(blocker.get("terminal_step") or "task_collect") if blocker else "task_collect"
            reason = f"本轮共处理 {total} 个任务：成功 {successes} 个，需人工介入 {manuals} 个，失败或未完成 {failures} 个。"
            if blocker and blocker["terminal_reason"]:
                reason += " 首个未成功任务：" + blocker["carrier"] + " / " + blocker["biztype"] + " — " + blocker["terminal_reason"]
            next_action = str(blocker.get("next_action") or "检查各任务终态报告，按团队流程完成人工评审与合并。") if blocker else "所有任务均已完成交付准备；按团队流程完成人工评审与合并。"

        report = {
            "report_version": 1, "report_kind": "run_final", "run_id": run_id,
            "result": result, "status": result, "terminal_step": terminal_step,
            "terminal_reason": reason, "next_action": next_action, "finished_at": now,
            "task_count": len(task_results), "task_results": task_results,
        }
        _atomic_json(reports_dir / "final.json", report)
    except Exception:
        logger.exception("写入 Run 级终态报告失败 run_id=%s", run_id)


def _atomic_json(path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    """命令行执行整张工作流。"""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="执行完整修复工作流")
    parser.add_argument("--run-id", default="", help="指定执行号；缺省自动生成")
    args = parser.parse_args()
    print(json.dumps(run_workflow(args.run_id or None), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()


__all__ = ["main", "prepare_run", "run_workflow"]
