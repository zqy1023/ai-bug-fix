"""Agent Provider 响应解析与审计辅助逻辑。"""

from __future__ import annotations

from typing import Any

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


__all__ = ["audit_metadata", "parse_result", "raw_result", "usage_of"]
