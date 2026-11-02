"""统一分析修复 Agent 的模型调用与交互提供者。"""

from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agents.unified_fixer.models import UnifiedFixerConfig
from core.contracts import AgentRequest
from core.policy import ToolPolicy
from infrastructure.llm.common import invoke_with_audit
from infrastructure.llm.session import ClaudeSession, run_agent_once

# 导入时捕获真实类型：测试会替换模块属性指向替身，判定不能用被替换后的名字。
_SESSION_TYPE = ClaudeSession


@dataclass(frozen=True)
class UnifiedFixerProvider:
    """拥有本任务会话生命周期的可调用通道。"""

    invoke: Callable[[AgentRequest], dict[str, Any]]
    closer: Callable[[], None]

    def __call__(self, request: AgentRequest) -> dict[str, Any]:
        return self.invoke(request)

    def close(self) -> None:
        """释放任务会话，禁止泄漏到另一轮修复。"""
        self.closer()


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
    )
    return any(marker in diagnostic for marker in markers) or isinstance(
        error, (TimeoutError, ConnectionError)
    )


def _interrupted_call_fallback(
    error: BaseException, request: AgentRequest, attempts: int
) -> dict[str, Any]:
    detail = " ".join(str(error).split())[:600]
    timed_out = isinstance(error, TimeoutError) or "AGENT_TIMEOUT" in detail
    cause = "模型调用超时" if timed_out else "模型调用中断"
    reason = (
        f"统一分析修复请求在 {attempts} 次尝试后因{cause}未能完成："
        f"{type(error).__name__}: {detail}；本次未取得分析结论。"
    )[:1800]
    logging.getLogger(__name__).warning(
        "统一分析修复网络中断 task_id=%s attempts=%d error=%s",
        request.task_id, attempts, detail,
    )
    return {
        "status": "MANUAL",
        "failure_kind": "agent_timeout" if timed_out else "agent_invocation_failed",
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
) -> UnifiedFixerProvider:
    """创建统一分析修复 Agent 的调用函数。

    这里的 ``timeout_seconds`` 是**整个阶段**的墙上时钟上限，不是单次调用的上限。
    语法自愈和网络重试都会重建 ``ClaudeSession``，如果每次重建都拿到全新的
    ``timeout_seconds``，一个任务就能挂到 (自愈轮数 + 2) × 重试次数 × 上限；
    因此 deadline 只在这里算一次，每轮只在剩余时间内调用。
    """
    active_config = config or UnifiedFixerConfig.load()
    invoke_runner = runner or run_agent_once
    session: ClaudeSession | None = None
    deadline = time.monotonic() + active_config.timeout_seconds

    def close() -> None:
        nonlocal session
        if session is not None:
            session.close()
            session = None

    def provider(request: AgentRequest) -> dict[str, Any]:
        ToolPolicy.validate(request)

        def _execute(req: AgentRequest) -> dict[str, Any]:
            nonlocal session
            prompt = req.system_prompt
            if req.prompt:
                prompt = f"{prompt}\n\n---\n\n{req.prompt}"

            remaining = deadline - time.monotonic()
            if remaining <= 1:
                # 阶段总预算已耗尽：不再发起调用，明确记为超时而不是模型失败。
                raise TimeoutError(
                    f"统一分析修复阶段总预算 {active_config.timeout_seconds} 秒已耗尽"
                )
            timeout = max(1, min(active_config.timeout_seconds, math.ceil(remaining)))
            max_turns = req.budget.get("max_turns", active_config.max_turns)
            max_tool_calls = req.budget.get("max_tool_calls", active_config.max_tool_calls)
            writable = bool(req.allowed_write_roots)

            # 统一 Agent 根据是否有写权限注入工具
            tools = ["Read", "Glob", "Grep"]
            if writable:
                tools.extend(["Write", "Edit"])

            if runner is None:
                if session is None:
                    from infrastructure.llm.output_schema import output_schema
                    session = ClaudeSession(
                        req.workspace,
                        {"ANTHROPIC_MODEL": req.model, "AI_REPAIR_TASK_ID": req.task_id,
                         "AI_REPAIR_CARRIER": str(req.prompt_context.get("carrier") or "")},
                        req.system_prompt, tools=tools, writable=writable,
                        max_turns=max_turns, max_tool_calls=max_tool_calls,
                        timeout_seconds=timeout, cumulative_budget=True,
                        output_schema=output_schema("unified_fixer"),
                    )
                # 自愈轮次复用同一会话时只发增量上下文：完整上下文（可达 120k 字符）
                # 每轮重发既贵又会让模型把旧结论与新反馈混读。首轮仍是完整输入。
                from infrastructure.llm.session_context import invoke_session

                def _call(sent: AgentRequest) -> dict[str, Any]:
                    return session.invoke(
                        sent.prompt, req.workspace,
                        allowed_roots=req.allowed_read_roots,
                        allowed_write_roots=req.allowed_write_roots or (req.workspace,),
                    )

                # 只有真会话才走增量通道；替身对象没有上下文记录，直接发完整请求。
                if isinstance(session, _SESSION_TYPE):
                    result = invoke_session(req, session, _call)
                else:
                    result = _call(req)
                if result.get("passed") is False and result.get("reason"):
                    detail = str(result.get("failure_detail") or result["reason"])
                    if _is_transient_error(RuntimeError(detail)) or result["reason"] == "AGENT_TIMEOUT":
                        close()
                        raise ConnectionError(detail)
                return result

            return invoke_runner(
                prompt=prompt,
                workspace=req.workspace,
                env={"ANTHROPIC_MODEL": req.model, "AI_REPAIR_TASK_ID": req.task_id,
                     "AI_REPAIR_CARRIER": str(req.prompt_context.get("carrier") or "")},
                writable=writable,
                tools=tools,
                max_turns=max_turns,
                max_tool_calls=max_tool_calls,
                timeout_seconds=timeout,
                allowed_roots=req.allowed_read_roots,
                allowed_write_roots=req.allowed_write_roots,
            )

        def invoke() -> dict[str, Any]:
            for attempt in range(1, active_config.max_provider_attempts + 1):
                try:
                    return _execute(request)
                except Exception as error:
                    if not _is_transient_error(error):
                        raise
                    if attempt == active_config.max_provider_attempts or deadline - time.monotonic() <= 1:
                        payload = _interrupted_call_fallback(error, request, attempt)
                        return {"result": json.dumps(payload, ensure_ascii=False), "passed": False,
                                "reason": payload["summary"], "failure_kind": payload["failure_kind"]}
            raise RuntimeError("Agent retry loop exhausted")

        return invoke_with_audit(request, invoke)

    return UnifiedFixerProvider(provider, close)


__all__ = ["create_provider"]
