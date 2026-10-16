"""Agent Provider 响应解析与审计辅助逻辑。"""

from __future__ import annotations

from typing import Any, Callable
import json
import threading
import time
import uuid
from pathlib import Path

_AUDIT_LOCK = threading.Lock()

from app.domain.json_text import loads_object


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
        "prompt_hash": request.prompt_hash,
        "model": request.model,
        "run_id": request.run_id,
        "task_id": request.task_id,
        "attempt": request.attempt,
        "allowed_read_roots": list(request.allowed_read_roots),
        "allowed_write_roots": list(request.allowed_write_roots),
        "tool_call_count": int(tool_call_count),
        "total_tokens": int(usage.get("total_tokens") or 0),
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
        usage = usage_of(raw)
        record = {
            "model_call_id": call_id, "run_id": str(getattr(request, "run_id", "") or ""),
            "task_id": str(getattr(request, "task_id", "") or ""),
            "agent_role": str(getattr(request, "agent_role", "") or ""),
            "attempt": int(getattr(request, "attempt", 1) or 1),
            "provider": str(getattr(request, "provider", "") or ""),
            "model": str(getattr(request, "model", "") or ""),
            "prompt_version": str(getattr(request, "prompt_version", "") or ""),
            "prompt_hash": str(getattr(request, "prompt_hash", "") or ""),
            "started_at": started_at, "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "status": "failed" if raw.get("passed") is False and raw.get("reason") else "succeeded",
            "input_tokens": usage.get("input_tokens", usage.get("input", None)),
            "output_tokens": usage.get("output_tokens", usage.get("output", None)),
            "cache_tokens": usage.get("cache_read_input_tokens", usage.get("cache_tokens", None)),
            "total_tokens": usage.get("total_tokens", None),
            "tool_call_count": usage.get("tool_calls", None),
            "prompt_chars": usage.get("prompt_chars"),
            "result_chars": usage.get("result_chars"),
            "events": _audit_events(raw.get("messages")),
            "events_path": "",
            "event_count": 0,
            "output_preview": raw_result(raw)[:4000],
            "error_type": str(raw.get("reason") or "")[:300] if raw.get("passed") is False else "",
            "finished_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }
        event_path, event_count = _persist_agent_events(record, record["events"])
        record["events_path"] = event_path
        record["event_count"] = event_count
        _persist_model_call(record)
        return raw
    except Exception as exc:
        status = "failed"
        error_type = type(exc).__name__
        record = {
            "model_call_id": call_id, "run_id": str(getattr(request, "run_id", "") or ""),
            "task_id": str(getattr(request, "task_id", "") or ""),
            "agent_role": str(getattr(request, "agent_role", "") or ""),
            "attempt": int(getattr(request, "attempt", 1) or 1),
            "provider": str(getattr(request, "provider", "") or ""),
            "model": str(getattr(request, "model", "") or ""),
            "prompt_version": str(getattr(request, "prompt_version", "") or ""),
            "prompt_hash": str(getattr(request, "prompt_hash", "") or ""),
            "started_at": started_at, "finished_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "status": status, "error_type": error_type,
            "events": _audit_events(getattr(exc, "agent_events", [])),
            "events_path": "",
            "event_count": 0,
        }
        event_path, event_count = _persist_agent_events(record, record["events"])
        record["events_path"] = event_path
        record["event_count"] = event_count
        _persist_model_call(record)
        raise


def _persist_agent_events(
    record: dict[str, Any], events: list[dict[str, Any]]
) -> tuple[str, int]:
    """将有效公开事件按任务、调用拆分持久化；不写隐藏思考或原始 SDK 对象。"""
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


def _audit_events(value: Any) -> list[dict[str, Any]]:
    """保留可审计的公开对话事件；不记录系统提示词或隐藏思考链。"""
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value[:100]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        if kind == "assistant":
            text = str(item.get("text") or "").strip()
            if text:
                result.append({"type": "assistant", "text": text[:2000], "elapsed_ms": item.get("elapsed_ms")})
        elif kind == "tool_use":
            result.append({
                "type": "tool_use",
                "tool": str(item.get("tool") or "unknown")[:80],
                "input": item.get("input") if isinstance(item.get("input"), dict) else {},
                "elapsed_ms": item.get("elapsed_ms"),
            })
        elif kind == "tool_result":
            result.append({
                "type": "tool_result",
                "is_error": bool(item.get("is_error")),
                "text": str(item.get("text") or "")[:800],
                "elapsed_ms": item.get("elapsed_ms"),
            })
    return result


def _persist_model_call(record: dict[str, Any]) -> None:
    run_id = str(record.get("run_id") or "")
    if not run_id or any(part in run_id for part in ("/", "\\", "..")):
        return
    try:
        from app.infrastructure.config import load_config
        target = Path(load_config().runtime_dir).resolve() / "runs" / run_id / "model_calls.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with _AUDIT_LOCK:
            with target.open("a", encoding="utf-8") as stream:
                stream.write(line + chr(10))
    except Exception:
        # 审计落盘故障不能覆盖原始模型结果；系统日志会暴露该缺口。
        import logging
        logging.getLogger(__name__).exception("模型调用审计写入失败 run_id=%s", run_id)


__all__ = ["audit_metadata", "parse_result", "raw_result", "usage_of", "invoke_with_audit"]
