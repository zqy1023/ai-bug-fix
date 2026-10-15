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
    """构造有限、可审计的上下文，不把完整状态或历史消息交给模型。

    ``symbols`` / ``category`` 来自确定性预检：预检已经算好了"文件 → 符号"映射，
    这里必须一并交给模型，否则模型只能盲读文件，浪费回合预算。
    """
    payload = {
        "problem": {str(key): str(value)[:1000] for key, value in problem.items()},
        "candidate_files": [str(item) for item in candidates[:max_candidates]],
        "retry_reason": str(retry_reason)[:2000],
        "prior_evidence": [str(item)[:500] for item in (prior_evidence or [])[:8]],
    }
    if category:
        payload["category"] = str(category)[:120]
    if isinstance(symbols, dict) and symbols:
        payload["file_symbols"] = {
            str(path): [str(name) for name in list(names or [])[:24]]
            for path, names in list(symbols.items())[:max_candidates]
            if names
        }
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return text[:max_chars]
