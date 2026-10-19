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
            "prompt_chars": usage.get("prompt_chars", len(str(getattr(request, "system_prompt", "") or "")) + len(str(getattr(request, "prompt", "") or ""))),
            "result_chars": usage.get("result_chars"),
            **_prompt_metrics(request),
            "events": _audit_events(raw.get("messages")),
            "events_path": "",
            "event_count": 0,
            "output_preview": _audit_text(raw_result(raw), 500),
            "error_type": str(raw.get("reason") or "")[:300] if raw.get("passed") is False else "",
            "finished_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }
        final_text = _audit_text(raw_result(raw), 3000)
        if final_text:
            record["events"].append({"type": "assistant", "text": final_text, "final": True})
        event_path, event_count = _persist_agent_events(record, record["events"])
        record["events_path"] = event_path
        record["event_count"] = event_count
        record["event_persistence_status"] = (
            "persisted" if event_count > 0 and event_path
            else "failed" if record["events"] and not event_path
            else "empty"
        )
        # 事件只在任务级审计文件保存，Run 级调用记录保留引用与计数，避免双份存储。
        record["events"] = []
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
            "persisted" if event_count > 0 and event_path
            else "failed" if record["events"] and not event_path
            else "empty"
        )
        record["events"] = []
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


def _audit_text(value: Any, limit: int) -> str:
    """裁剪审计文本并遮蔽常见凭据格式，避免工具输出把密钥写进运行记录。"""
    text = str(value or "").strip()
    text = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", r"\1[REDACTED]", text)
    text = re.sub(
        r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\s*[:=]\s*)['\"]?[^\s,'\"]+",
        r"\1[REDACTED]",
        text,
    )
    return text[:limit]


_SAFE_TOOL_INPUT_KEYS = frozenset({
    "file_path", "notebook_path", "path", "pattern", "glob", "description", "skill", "url", "query",
})


def _safe_tool_input(value: Any) -> dict[str, Any]:
    """仅保留定位工具调用所需的短字段；不保存 prompt、正文、命令或编辑前后文。"""
    if not isinstance(value, dict):
        return {}
    safe: dict[str, Any] = {}
    for key in _SAFE_TOOL_INPUT_KEYS:
        item = value.get(key)
        if item in (None, "", [], {}):
            continue
        if isinstance(item, str):
            safe[key] = _audit_text(item, 240)
        elif isinstance(item, (int, float, bool)):
            safe[key] = item
        elif isinstance(item, list):
            safe[key] = [_audit_text(part, 120) for part in item[:5] if isinstance(part, str)]
    return safe


def _audit_events(value: Any) -> list[dict[str, Any]]:
    """保留公开助手与工具事件；隐藏思考和未知事件不入库。"""
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value[:200]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        # SDK 流式收集的助手事件带 elapsed_ms，视为中间分析；无计时的公开摘要可保留。
        if kind == "assistant" and "elapsed_ms" not in item:
            text = _audit_text(item.get("text") or "", 500)
            if text and "分析" not in text and "analysis" not in text.casefold():
                result.append({"type": "assistant", "text": text})
        elif kind == "tool_use":
            result.append({
                "type": "tool_use",
                "tool": str(item.get("tool") or "unknown")[:80],
                "input": _safe_tool_input(item.get("input")),
                "elapsed_ms": item.get("elapsed_ms"),
            })
        elif kind == "tool_result":
            result.append({
                "type": "tool_result",
                "is_error": bool(item.get("is_error")),
                "text": _audit_text(item.get("text") or "", 500),
                "elapsed_ms": item.get("elapsed_ms"),
            })
        if len(result) >= 99:
            break
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
