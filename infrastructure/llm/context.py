"""从确定性工作流状态构造 Agent Prompt 上下文与预算裁剪。"""

from __future__ import annotations

import json
from typing import Any

from core.contracts import AgentRole
from infrastructure.llm.models import PromptContext


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

    _MAX_CANDIDATE_PATH_CHARS = 500
    _MAX_SYMBOL_NAME_CHARS = 200

    def _problem_value(value: Any) -> str:
        if isinstance(value, str):
            return value[:1000]
        try:
            return json.dumps(value, ensure_ascii=False)[:1000]
        except (TypeError, ValueError):
            return str(value)[:1000]

    payload: dict[str, Any] = {
        "problem": {
            str(key): _problem_value(value)
            for key, value in list(problem.items())[:24]
        },
        "candidate_files": [str(item)[:_MAX_CANDIDATE_PATH_CHARS] for item in candidates[:max_candidates]],
        "retry_reason": str(retry_reason)[:2000],
        "prior_evidence": [str(item)[:500] for item in (prior_evidence or [])[:8]],
        "context_truncated": False,
    }
    if category:
        payload["category"] = str(category)[:120]
    if isinstance(symbols, dict) and symbols:
        payload["file_symbols"] = {
            str(path)[:_MAX_CANDIDATE_PATH_CHARS]: [
                str(name)[:_MAX_SYMBOL_NAME_CHARS] for name in list(names or [])[:24]
            ]
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


def prompt_context(
    role: AgentRole,
    state: dict[str, Any],
    *,
    framework: str,
    evidence: dict[str, Any],
    read_roots: tuple[str, ...],
    write_roots: tuple[str, ...],
    budget: dict[str, int],
    retry_feedback: str = "",
    source_snapshot: dict[str, Any] | None = None,
    changed_files: tuple[str, ...] = (),
    diff_summary: str = "",
    allowed_scope: tuple[str, ...] = (),
    stop_conditions: tuple[str, ...] = (),
    source_project_path: str = "",
    project_rules: str = "",
) -> PromptContext:
    """从 ES 原始问题和图身份字段创建不可变 PromptContext。"""
    problem = state.get("problem")
    if not isinstance(problem, dict):
        raise ValueError("Agent 输入缺少原始 problem 对象")
    required = {key: problem.get(key) for key in ("service_name", "biztype", "carrier")}
    for key, value in required.items():
        if not isinstance(value, str) or not value:
            raise ValueError(f"Agent 输入的原始 problem 缺少 {key}")
    run_id = state.get("run_id")
    task_id = state.get("task_id")
    attempt = state.get("attempt")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("Agent 输入缺少 run_id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("Agent 输入缺少 task_id")
    if not isinstance(attempt, int) or attempt < 1:
        raise ValueError("Agent 输入 attempt 必须是正整数")
    return PromptContext(
        agent_role=role,
        run_id=run_id,
        task_id=task_id,
        attempt=attempt,
        service_name=required["service_name"],
        biztype=required["biztype"],
        carrier=required["carrier"],
        framework=framework,
        problem=problem,
        source_snapshot=source_snapshot or {},
        evidence={**evidence, **({"previous_attempt": state["retry_context"]} if state.get("retry_context") else {})},
        changed_files=changed_files,
        diff_summary=diff_summary,
        allowed_scope=allowed_scope,
        stop_conditions=stop_conditions,
        allowed_read_roots=read_roots,
        allowed_write_roots=write_roots,
        budget=budget,
        retry_feedback=retry_feedback,
        source_project_path=source_project_path,
        project_rules=project_rules,
    )


def model_name(runtime: Any) -> str:
    """从 provider 注入环境或应用配置获取实际模型名。"""
    environment = getattr(runtime, "env", None)
    model = environment.get("ANTHROPIC_MODEL", "") if isinstance(environment, dict) else ""
    if not model:
        from infrastructure.llm.config import load_config

        model = load_config().claude.model
    if not model:
        raise ValueError("缺少 ANTHROPIC_MODEL，不能生成可审计的 Agent 请求")
    return model


__all__ = ["build_context", "model_name", "prompt_context"]
