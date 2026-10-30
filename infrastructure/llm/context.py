"""从确定性工作流状态构造 Agent Prompt 上下文与预算裁剪。"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
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


def compact_roots(roots: Sequence[str], workspace: str | Path) -> tuple[str, ...]:
    """压缩只读范围：去掉被父路径覆盖的重复项，工作区内改成相对路径。

    hook 用真实绝对路径做强制校验，提示词只需要让模型知道"可读哪里"。把 7 条
    绝对路径原样塞进上下文（实测 1KB），既占体积又不提供额外信息。
    """
    resolved: list[Path] = []
    for item in roots:
        text = str(item or "").strip()
        if not text:
            continue
        try:
            resolved.append(Path(text).resolve())
        except OSError:
            continue
    kept: list[Path] = []
    for path in sorted(set(resolved), key=lambda value: len(value.parts)):
        if any(path == other or other in path.parents for other in kept):
            continue
        kept.append(path)
    root = Path(workspace).resolve() if str(workspace).strip() else None
    compact: list[str] = []
    for path in kept:
        if root is not None:
            try:
                compact.append(path.relative_to(root).as_posix() or ".")
                continue
            except ValueError:
                pass
        compact.append(path.as_posix())
    return tuple(compact)


def compact_problem(problem: Mapping[str, Any]) -> dict[str, Any]:
    """去掉 problem 里完全重复的原始文本。

    解析器在没有 `` error:`` 标记时会把整行日志回退成 error，于是
    ``error``/``raw_error``/``message`` 三份完全相同——实测占 783 字符。
    ``message`` 保留（规则要求"优先阅读 problem.message 全文"），其余重复项删除。
    """
    value = dict(problem)
    message = str(value.get("message") or "")
    error = str(value.get("error") or "")
    raw = str(value.get("raw_error") or "")
    if raw and raw in {message, error}:
        value.pop("raw_error", None)
    if error and message and error == message:
        value.pop("error", None)
    return value


def read_scope(context: PromptContext, workspace: str | Path) -> dict[str, Any]:
    """把读写范围压缩成"根 + 子路径"结构，供渲染。

    5 条共享树绝对路径实测占 768 字符，压缩后约 190。请求契约仍保留真实路径，
    这里只决定模型看到什么。
    """
    root = Path(workspace).resolve() if str(workspace).strip() else None
    inside: list[str] = []
    outside: list[Path] = []
    for item in context.allowed_read_roots:
        try:
            path = Path(str(item)).resolve()
        except OSError:
            continue
        if root is not None and path.is_relative_to(root):
            inside.append(path.relative_to(root).as_posix())
        else:
            outside.append(path)
    scope: dict[str, Any] = {
        "workspace_root": root.as_posix() if root is not None else "",
        "carrier": [p for p in inside if not p.startswith(".evidence/")],
        "evidence": [p for p in inside if p.startswith(".evidence/")],
        "writable": list(compact_roots(context.allowed_write_roots, workspace)),
    }
    if outside:
        try:
            common = Path(os.path.commonpath([str(path) for path in outside]))
        except ValueError:
            scope["shared_root"] = ""
            scope["shared_paths"] = [path.as_posix() for path in outside]
        else:
            if len(outside) > 1:
                scope["shared_root"] = common.as_posix()
                scope["shared_paths"] = [p.relative_to(common).as_posix() for p in outside]
            else:
                scope["shared_root"] = outside[0].as_posix()
                scope["shared_paths"] = []
    return scope


def display_context(context: PromptContext, workspace: str | Path) -> PromptContext:
    """构造只用于渲染的上下文副本：压缩体积，但不动权限字段。

    ``allowed_read_roots`` 在请求契约里必须保持真实路径（ToolPolicy 与 PreToolUse
    hook 依赖它做强制校验），因此只在渲染副本里替换成相对范围。
    """
    evidence = dict(context.evidence)
    full = evidence.get("full_context")
    if isinstance(full, dict):
        # notice 文案已经在 core 规则里说明，逐调用重复没有信息增量。
        evidence["full_context"] = {k: v for k, v in full.items() if k != "notice"}
    snapshot = dict(context.source_snapshot)
    return context.model_copy(update={
        "problem": compact_problem(context.problem),
        "evidence": evidence,
        "source_snapshot": snapshot,
        "allowed_read_roots": compact_roots(context.allowed_read_roots, workspace),
        "allowed_write_roots": compact_roots(context.allowed_write_roots, workspace),
        "source_project_path": _compact_path(context.source_project_path, workspace),
        "read_scope": read_scope(context, workspace),
    })


def _compact_path(value: str, workspace: str | Path) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    root = Path(workspace).resolve() if str(workspace).strip() else None
    try:
        path = Path(text).resolve()
    except OSError:
        return text
    if root is not None and path.is_relative_to(root):
        return path.relative_to(root).as_posix()
    return path.as_posix()


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
