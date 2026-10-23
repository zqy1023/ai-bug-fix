"""Agent Provider 响应解析与审计辅助逻辑。"""

from __future__ import annotations

from typing import Any, Callable
import json
import re
import threading
import time
import uuid
from pathlib import Path

_AUDIT_LOCK = threading.Lock()

from app.domain.json_text import loads_object
from config.settings import settings
from app.infrastructure.records.policy import debug_artifacts
from app.infrastructure.records.database import RecordDatabase


def parse_result(raw: dict[str, Any], role: str) -> dict[str, Any]:
    """将 Provider 返回的 JSON 转成业务对象，并拒绝缺失或非法响应。

    只返回业务字段：``usage`` 是调用统计元数据，由 :func:`usage_of` 单独取出。
    元数据一旦混进业务 payload，就会撞上业务契约的 ``extra="forbid"``，
    让「模型调用成功、结论合法」被误判成契约违约。
    """
    if role == "review" and "result" not in raw and (
        "passed" in raw or "findings" in raw
    ):
        return dict(raw)
    text = raw.get("result")
    if not isinstance(text, str) or not text.strip():
        return {"passed": False, "reason": f"{role} agent 未返回结构化结果"}
    payload = loads_object(text)
    if payload is None:
        return {"passed": False, "reason": f"{role} agent 返回不是有效 JSON"}
    return payload


def raw_result(raw: dict[str, Any]) -> str:
    """取出抽 JSON 之前的模型原文。不是字符串时记空，不把包装对象当原文。"""
    text = raw.get("result")
    return text if isinstance(text, str) else ""


def usage_of(raw: dict[str, Any]) -> dict[str, Any]:
    """取出 Provider 的调用统计元数据；缺失或非法时为空字典。"""
    usage = raw.get("usage")
    return dict(usage) if isinstance(usage, dict) else {}


def audit_metadata(
    request: Any,
    usage: dict[str, Any],
) -> dict[str, Any]:
    """提取不含 Prompt 正文和任务敏感数据的调用审计字段。"""
    tool_call_count = usage.get("tool_calls", 0)
    return {
        "agent_role": request.agent_role,
        "prompt_version": request.prompt_version,
        "prompt_hash": usage.get("sent_prompt_hash") or request.prompt_hash,
        "sent_prompt_hash": usage.get("sent_prompt_hash") or request.prompt_hash,
        "logical_context_hash": usage.get("logical_context_hash") or request.logical_context_hash or request.prompt_hash,
        "context_mode": usage.get("context_mode", request.context_mode),
        "model_call_id": usage.get("model_call_id", ""),
        "provider": usage.get("provider", request.provider),
        "model": request.model,
        "run_id": request.run_id,
        "task_id": request.task_id,
        "attempt": request.attempt,
        "allowed_read_roots": list(request.allowed_read_roots),
        "allowed_write_roots": list(request.allowed_write_roots),
        "tool_call_count": int(tool_call_count),
        "total_tokens": usage.get("total_tokens"),
        "tool_allowed": usage.get("tool_allowed"),
        "tool_denied": usage.get("tool_denied"),
        "tool_completed": usage.get("tool_completed"),
    }


def _prompt_metrics(request: Any) -> dict[str, int]:
    """只记录 Prompt 各组成部分的字符数，不持久化 Prompt 正文。"""
    context = getattr(request, "prompt_context", {})
    if not isinstance(context, dict):
        context = {}

    def size(value: Any) -> int:
        try:
            return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        except (TypeError, ValueError):
            return 0

    system_prompt = str(getattr(request, "system_prompt", "") or "")
    common_prompt, separator, role_prompt = system_prompt.partition(chr(10) * 2 + "---" + chr(10) * 2)
    return {
        "system_prompt_chars": len(system_prompt),
        "common_rules_chars": len(common_prompt) if separator else len(system_prompt),
        "role_prompt_chars": len(role_prompt) if separator else 0,
        "task_context_chars": len(str(getattr(request, "prompt", "") or "")),
        "problem_chars": size(context.get("problem")),
        "source_snapshot_chars": size(context.get("source_snapshot")),
        "evidence_chars": size(context.get("evidence")),
        "identity_chars": size({
            key: context.get(key)
            for key in ("run_id", "task_id", "attempt", "service_name", "biztype", "carrier", "framework")
            if key in context
        }),
    }


def invoke_with_audit(request: Any, invoke: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """记录一次真实 provider 请求的元数据；不持久化 prompt 正文或密钥。"""
    started = time.perf_counter()
    started_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    call_id = uuid.uuid4().hex
    status = "succeeded"
    error_type = ""
    try:
        raw = invoke()
        raw = raw if isinstance(raw, dict) else {}
        usage = {**usage_of(raw), "model_call_id": call_id, "sent_prompt_hash": request.prompt_hash,
                 "logical_context_hash": request.logical_context_hash or request.prompt_hash,
                 "context_mode": request.context_mode, "provider": request.provider}
        raw["usage"] = usage
        record = {
            "model_call_id": call_id, "run_id": str(getattr(request, "run_id", "") or ""),
            "task_id": str(getattr(request, "task_id", "") or ""),
            "agent_role": str(getattr(request, "agent_role", "") or ""),
            "attempt": int(getattr(request, "attempt", 1) or 1),
            "provider": str(getattr(request, "provider", "") or ""),
            "model": str(getattr(request, "model", "") or ""),
            "prompt_version": str(getattr(request, "prompt_version", "") or ""),
            "prompt_hash": request.prompt_hash, "sent_prompt_hash": request.prompt_hash,
            "logical_context_hash": request.logical_context_hash or request.prompt_hash,
            "context_mode": request.context_mode, "previous_context_hash": request.previous_context_hash,
            "started_at": started_at, "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "status": "failed" if raw.get("passed") is False and raw.get("reason") else "succeeded",
            "input_tokens": usage.get("input_tokens", usage.get("input", None)),
            "output_tokens": usage.get("output_tokens", usage.get("output", None)),
            "cache_tokens": usage.get("cache_read_input_tokens", usage.get("cache_tokens", None)),
            "total_tokens": usage.get("total_tokens", None),
            "tool_call_count": usage.get("tool_calls", None),
            **{key: usage.get(key) for key in ("tool_attempts", "tool_allowed", "tool_denied", "tool_completed",
                                               "tool_errors", "structured_output_calls", "tool_stop_kind", "tool_stop_reason")},
            "sdk_receipt": raw.get("sdk_receipt") or {},
            "usage_source": "sdk_reported" if isinstance(usage.get("input_tokens"), int) else "unknown",
            "token_total_scope": usage.get("token_total_scope", "unknown"),
            "error_detail": raw.get("failure_detail") or raw.get("reason") or "",
            "prompt_chars": usage.get("prompt_chars", len(str(getattr(request, "system_prompt", "") or "")) + len(str(getattr(request, "prompt", "") or ""))),
            "result_chars": usage.get("result_chars"),
            **_prompt_metrics(request),
            "events": _audit_events(raw.get("messages")),
            "events_path": "",
            "event_count": 0,
            "output_preview": _audit_text(raw_result(raw), 500),
            # 类别用于统计，完整原因另存 error_detail。
            "error_type": "provider_error" if raw.get("passed") is False and raw.get("reason") else "",
            "finished_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }
        final_text = raw_result(raw)
        if final_text:
            record["events"].append({"type": "assistant", "text": final_text, "final": True})
        event_path, event_count = _persist_agent_events(record, record["events"])
        record["events_path"] = event_path
        record["event_count"] = event_count
        record["event_persistence_status"] = (
            "disabled" if not _debug_for_record(record)
            else "persisted" if event_count > 0 and event_path
            else "failed" if record["events"] and not event_path
            else "empty"
        )
        # 事件只在任务级审计文件保存，Run 级调用记录保留引用与计数，避免双份存储。
        record["events"] = []
        record["prompt_snapshot"] = _persist_prompt_snapshot(record, request)
        _persist_model_call(record)
        return raw
    except Exception as exc:
        status = "failed"
        error_type = type(exc).__name__
        failed_usage = getattr(exc, "agent_usage", {})
        failed_usage = {**failed_usage, "model_call_id": call_id, "sent_prompt_hash": request.prompt_hash,
                        "logical_context_hash": request.logical_context_hash or request.prompt_hash,
                        "context_mode": request.context_mode, "provider": request.provider}
        try:
            setattr(exc, "agent_usage", failed_usage)
        except Exception:
            pass
        record = {
            "model_call_id": call_id, "run_id": str(getattr(request, "run_id", "") or ""),
            "task_id": str(getattr(request, "task_id", "") or ""),
            "agent_role": str(getattr(request, "agent_role", "") or ""),
            "attempt": int(getattr(request, "attempt", 1) or 1),
            "provider": str(getattr(request, "provider", "") or ""),
            "model": str(getattr(request, "model", "") or ""),
            "prompt_version": str(getattr(request, "prompt_version", "") or ""),
            "prompt_hash": request.prompt_hash, "sent_prompt_hash": request.prompt_hash,
            "logical_context_hash": request.logical_context_hash or request.prompt_hash,
            "context_mode": request.context_mode, "previous_context_hash": request.previous_context_hash,
            "started_at": started_at, "finished_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "status": status, "error_type": error_type, "error_detail": str(exc),
            "sdk_receipt": getattr(exc, "sdk_receipt", {}),
            "input_tokens": failed_usage.get("input_tokens"), "output_tokens": failed_usage.get("output_tokens"),
            "cache_tokens": failed_usage.get("cache_read_input_tokens"), "total_tokens": failed_usage.get("total_tokens"),
            "usage_source": "sdk_reported" if isinstance(failed_usage.get("input_tokens"), int) else "unknown",
            **{key: failed_usage.get(key) for key in ("tool_allowed", "tool_denied", "tool_completed", "tool_stop_kind", "tool_stop_reason")},
            "prompt_chars": len(str(getattr(request, "system_prompt", "") or "")) + len(str(getattr(request, "prompt", "") or "")),
            **_prompt_metrics(request),
            "events": _audit_events(getattr(exc, "agent_events", [])),
            "events_path": "",
            "event_count": 0,
        }
        event_path, event_count = _persist_agent_events(record, record["events"])
        record["events_path"] = event_path
        record["event_count"] = event_count
        record["event_persistence_status"] = (
            "disabled" if not _debug_for_record(record)
            else "persisted" if event_count > 0 and event_path
            else "failed" if record["events"] and not event_path
            else "empty"
        )
        record["events"] = []
        record["prompt_snapshot"] = _persist_prompt_snapshot(record, request)
        _persist_model_call(record)
        raise


def _debug_for_record(record: dict[str, Any]) -> bool:
    from app.infrastructure.config import load_config
    run_id = str(record.get("run_id") or "")
    if not run_id or any(part in run_id for part in ("/", "\\", "..")):
        return debug_artifacts()
    return debug_artifacts(Path(load_config().runtime_dir).resolve() / "runs" / run_id)


def _persist_agent_events(
    record: dict[str, Any], events: list[dict[str, Any]]
) -> tuple[str, int]:
    """将有效公开事件按任务、调用拆分持久化；不写隐藏思考或原始 SDK 对象。"""
    if not _debug_for_record(record):
        return "", 0
    run_id = str(record.get("run_id") or "")
    task_id = str(record.get("task_id") or "")
    call_id = str(record.get("model_call_id") or "")
    role = str(record.get("agent_role") or "")
    if (
        not run_id or not task_id or not call_id
        or "/" in run_id or "/" in task_id
        or chr(92) in run_id + task_id or ".." in run_id or ".." in task_id
    ):
        return "", 0
    try:
        from app.infrastructure.config import load_config
        runtime_root = Path(load_config().runtime_dir).resolve()
        run_root = runtime_root / "runs" / run_id
        target = run_root / "tasks" / task_id / "audit" / "agent_events.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        count = 0
        with _AUDIT_LOCK:
            with target.open("a", encoding="utf-8") as stream:
                for index, event in enumerate(events):
                    if not isinstance(event, dict):
                        continue
                    payload = {
                        "model_call_id": call_id,
                        "run_id": run_id,
                        "task_id": task_id,
                        "agent_role": role,
                        "attempt": int(record.get("attempt") or 1),
                        "event_index": index,
                        "recorded_at": now,
                        "event": event,
                    }
                    stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + chr(10))
                    count += 1
        return target.relative_to(run_root).as_posix(), count
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "Agent 事件轨迹持久化失败 run_id=%s task_id=%s call_id=%s",
            run_id, task_id, call_id,
        )
        return "", 0


def _persist_prompt_snapshot(record: dict[str, Any], request: Any) -> str:
    """把完整提示词快照写入任务审计目录；返回相对路径。

    ``model_calls.jsonl`` 只保存 hash 与字符数，无法支撑"查看提示词"。快照是审计
    快照保留原文，仅在任务级目录内落盘；页面预览与完整归档分开。
    """
    if not _debug_for_record(record) or not settings.workbench_prompt_snapshot:
        return ""
    run_id = str(record.get("run_id") or "")
    task_id = str(record.get("task_id") or "")
    call_id = str(record.get("model_call_id") or "")
    if (
        not run_id or not task_id or not call_id
        or "/" in run_id or "/" in task_id
        or chr(92) in run_id + task_id or ".." in run_id or ".." in task_id
    ):
        return ""
    try:
        from app.infrastructure.config import load_config

        run_root = Path(load_config().runtime_dir).resolve() / "runs" / run_id
        roots = [
            str(item)
            for item in (
                *tuple(getattr(request, "allowed_read_roots", ()) or ()),
                *tuple(getattr(request, "allowed_write_roots", ()) or ()),
            )
            if str(item)
        ]
        system_prompt = str(getattr(request, "system_prompt", "") or "")
        common, separator, role = system_prompt.partition(chr(10) * 2 + "---" + chr(10) * 2)
        context = getattr(request, "prompt_context", {})
        context_text = (
            json.dumps(context, ensure_ascii=False, default=str)
            if isinstance(context, dict) else str(context or "")
        )
        sections = {
            "system_prompt_common": common if separator else system_prompt,
            "system_prompt_role": role if separator else "",
            "task_prompt": str(getattr(request, "prompt", "") or ""),
            "prompt_context": context_text,
        }
        limit = settings.workbench_prompt_max_chars
        rendered: dict[str, str] = {}
        truncated = False
        for key, value in sections.items():
            text, was_cut = _snapshot_text(value, roots, limit)
            rendered[key] = text
            truncated = truncated or was_cut
        # 工作区可被回收；把压缩前完整任务证据一并归档，页面可直接展开。
        reference = (context.get("evidence") or {}).get("full_context") or {}
        if reference.get("path"):
            import hashlib
            from app.prompts.budget import readable_context
            full_path = Path(reference["path"]).resolve()
            owned = readable_context(Path(request.workspace).resolve(), run_id, task_id, request.agent_role)
            if full_path.parent not in owned or full_path.is_symlink():
                raise ValueError("完整上下文归档引用不属于本任务")
            full_text = full_path.read_text(encoding="utf-8")
            if hashlib.sha256(full_text.encode()).hexdigest() != reference.get("sha256"):
                raise ValueError("完整上下文归档哈希不一致")
            rendered["full_task_context"] = full_text
        payload = {
            "model_call_id": call_id,
            "run_id": run_id,
            "task_id": task_id,
            "agent_role": str(record.get("agent_role") or ""),
            "attempt": int(record.get("attempt") or 1),
            "model": str(record.get("model") or ""),
            "prompt_version": str(record.get("prompt_version") or ""),
            "prompt_hash": str(record.get("prompt_hash") or ""),
            **rendered,
            "chars": {key: len(value) for key, value in {**sections, **rendered}.items()},
            "truncated": truncated,
            "redacted": False,
            "context_mode": request.context_mode,
            "logical_context_hash": request.logical_context_hash or request.prompt_hash,
            "previous_context_hash": request.previous_context_hash,
        }
        target = run_root / "tasks" / task_id / "audit" / "prompts" / f"{call_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        temporary.replace(target)
        return target.relative_to(run_root).as_posix()
    except Exception:
        import logging

        logging.getLogger(__name__).exception(
            "提示词快照持久化失败 run_id=%s task_id=%s call_id=%s",
            run_id, task_id, call_id,
        )
        return ""


def _snapshot_text(value: str, roots: list[str], limit: int) -> tuple[str, bool]:
    """完整保留实际输入的字符串和路径；预算只限制发送，不裁剪审计副本。"""
    return str(value or ""), False


def _audit_text(value: Any, limit: int) -> str:
    """预览可限长，原始值不脱敏。"""
    return str(value or "")[:limit]


_SAFE_TOOL_INPUT_KEYS = frozenset({
    "file_path", "notebook_path", "path", "pattern", "glob", "description", "skill", "url", "query",
})


def _safe_tool_input(value: Any) -> dict[str, Any]:
    """保留完整公开工具入参。"""
    return dict(value) if isinstance(value, dict) else {}


def _audit_events(value: Any) -> list[dict[str, Any]]:
    """持久化公开工具证据和关联 ID；不记录隐藏思考。"""
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind in {"tool_use", "tool_result", "tool_decision"}:
            result.append(dict(item))
        elif kind == "assistant" and "elapsed_ms" not in item:
            result.append(dict(item))
    return result


def _persist_model_call(record: dict[str, Any]) -> None:
    run_id = str(record.get("run_id") or "")
    if not run_id or any(part in run_id for part in ("/", "\\", "..")):
        return
    try:
        from app.infrastructure.config import load_config
        run_dir = Path(load_config().runtime_dir).resolve() / "runs" / run_id
        RecordDatabase(run_dir).append(str(record.get("task_id") or ""), "model_calls", record)
    except Exception:
        # 审计落盘故障不能覆盖原始模型结果；系统日志会暴露该缺口。
        import logging
        logging.getLogger(__name__).exception("模型调用审计写入失败 run_id=%s", run_id)


__all__ = ["audit_metadata", "parse_result", "raw_result", "usage_of", "invoke_with_audit"]
