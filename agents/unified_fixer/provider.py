"""统一分析修复 Agent 的模型调用与交互提供者。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from infrastructure.llm.common import invoke_with_audit
from core.policy import ToolPolicy
from agents.unified_fixer.models import UnifiedFixerConfig
from core.contracts import AgentRequest
from infrastructure.llm.session import run_agent_once


def _is_transient_error(error: BaseException) -> bool:
    message = str(error).casefold()
    events = getattr(error, "agent_events", [])
    try:
        event_text = json.dumps(events, ensure_ascii=False, default=str).casefold()
    except Exception:
        event_text = str(events).casefold()
    diagnostic = message + "\n" + event_text
    markers = (
        "connection closed mid-response",
        "connection reset",
        "server disconnected",
        "incomplete chunked read",
        "remoteprotocolerror",
        "readtimeout",
        "connecttimeout",
        "broken pipe",
        "temporarily unavailable",
        "connection aborted",
        "error result: success",
    )
    return any(marker in diagnostic for marker in markers) or isinstance(
        error, (TimeoutError, ConnectionError)
    )


def _interrupted_call_fallback(
    error: BaseException, request: AgentRequest, attempts: int
) -> dict[str, Any]:
    reason = (
        f"统一分析修复请求在 {attempts} 次尝试后因网络中断未能完成：{type(error).__name__}；"
        "转人工处理。"
    )[:1800]
    return {
        "status": "MANUAL",
        "root_cause": "",
        "category": "unknown",
        "attribution_type": "pending",
        "facts": [],
        "source_evidence": [],
        "candidate_files": [],
        "proposed_edit_files": [],
        "changed_files": [],
        "acceptance_criteria": [],
        "self_check": {"syntax_valid": False, "reflection_notes": reason},
        "evidence_gaps": [reason],
        "recommended_scope": "manual",
        "summary": reason,
        "analysis": reason,
        "passed": False,
        "result": "failed",
    }


def create_provider(
    config: UnifiedFixerConfig | None = None,
    runner: Any = None,
) -> Any:
    """创建统一分析修复 Agent 的调用函数。"""
    active_config = config or UnifiedFixerConfig.load()
    invoke_runner = runner or run_agent_once

    def provider(request: AgentRequest) -> dict[str, Any]:
        ToolPolicy.validate(request)

        def _execute(req: AgentRequest) -> dict[str, Any]:
            prompt = req.system_prompt
            if req.prompt:
                prompt = f"{prompt}\n\n---\n\n{req.prompt}"

            timeout = active_config.timeout_seconds
            max_turns = req.budget.get("max_turns", active_config.max_turns)
            writable = bool(req.allowed_write_roots)

            # 统一 Agent 根据是否有写权限注入工具
            tools = ["Read", "Glob", "Grep"]
            if writable:
                tools.extend(["Write", "Edit"])

            return invoke_runner(
                prompt=prompt,
                workspace=req.workspace,
                env={"ANTHROPIC_MODEL": req.model, "AI_REPAIR_TASK_ID": req.task_id,
                     "AI_REPAIR_CARRIER": str(req.prompt_context.get("carrier") or "")},
                writable=writable,
                tools=tools,
                max_turns=max_turns,
                timeout_seconds=timeout,
                allowed_roots=req.allowed_read_roots,
                allowed_write_roots=req.allowed_write_roots,
            )

        def invoke() -> dict[str, Any]:
            for attempt in range(1, 3):
                try:
                    return _execute(request)
                except Exception as error:
                    if not _is_transient_error(error):
                        raise
                    if attempt == 2:
                        payload = _interrupted_call_fallback(error, request, attempt)
                        return {"result": json.dumps(payload, ensure_ascii=False)}
            raise RuntimeError("Agent retry loop exhausted")

        return invoke_with_audit(request, invoke)

    return provider


__all__ = ["create_provider"]
