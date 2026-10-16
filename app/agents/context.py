"""Agent 上下文预算和候选文件裁剪。"""

from __future__ import annotations

import json
from typing import Any


def build_context(
    problem: dict[str, Any],
    candidates: list[str],
    retry_reason: str = "",
    prior_evidence: list[str] | None = None,
    *,
    max_chars: int = 24000,
    max_candidates: int = 8,
    symbols: dict[str, list[str]] | None = None,
    category: str = "",
) -> str:
    """按证据优先级裁剪上下文，并始终返回合法 JSON。

    优先保留原始问题和目标文件；先删除低优先级证据，再裁剪符号和反馈。
    不直接切字符串尾部，避免产生半截 JSON 或截掉任务身份。
    """
    if max_chars < 256:
        raise ValueError("Agent 上下文预算至少需要 256 字符")

    payload: dict[str, Any] = {
        "problem": {
            str(key): str(value)[:1000]
            for key, value in list(problem.items())[:24]
        },
        "candidate_files": [str(item) for item in candidates[:max_candidates]],
        "retry_reason": str(retry_reason)[:2000],
        "prior_evidence": [str(item)[:500] for item in (prior_evidence or [])[:8]],
        "context_truncated": False,
    }
    if category:
        payload["category"] = str(category)[:120]
    if isinstance(symbols, dict) and symbols:
        payload["file_symbols"] = {
            str(path): [str(name) for name in list(names or [])[:24]]
            for path, names in list(symbols.items())[:max_candidates]
            if names
        }

    def encode() -> str:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    text = encode()
    if len(text) <= max_chars:
        payload.pop("context_truncated", None)
        return encode()

    payload["context_truncated"] = True
    while payload.get("prior_evidence") and len(encode()) > max_chars:
        payload["prior_evidence"].pop()

    symbols_payload = payload.get("file_symbols")
    if isinstance(symbols_payload, dict):
        for symbol_path in list(symbols_payload):
            while symbols_payload[symbol_path] and len(encode()) > max_chars:
                symbols_payload[symbol_path].pop()
            if not symbols_payload[symbol_path]:
                symbols_payload.pop(symbol_path, None)
            if len(encode()) <= max_chars:
                break
        if not symbols_payload:
            payload.pop("file_symbols", None)

    if len(encode()) > max_chars:
        payload["retry_reason"] = str(payload.get("retry_reason") or "")[:400]
    while len(payload["candidate_files"]) > 1 and len(encode()) > max_chars:
        payload["candidate_files"].pop()
    if len(encode()) > max_chars:
        payload["retry_reason"] = ""

    for limit in (500, 250, 120, 60):
        if len(encode()) <= max_chars:
            break
        payload["problem"] = {key: value[:limit] for key, value in payload["problem"].items()}
    if len(encode()) > max_chars:
        payload["problem"] = {
            key: value[:40] for key, value in list(payload["problem"].items())[:8]
        }
        payload["category"] = str(payload.get("category") or "")[:40]

    text = encode()
    if len(text) > max_chars:
        raise ValueError(
            f"Agent 上下文无法在预算内完整序列化：需要 {len(text)} 字符，预算 {max_chars}"
        )
    return text
