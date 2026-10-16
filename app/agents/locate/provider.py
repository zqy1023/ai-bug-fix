"""根因定位模型通道：仅提供有权限钩子的 Claude 一次性只读调用。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.agents.locate.config import LocateConfig
from app.agents.policy import ToolPolicy
from app.agents.common import invoke_with_audit
from app.application.contracts import AgentRequest
from app.infrastructure.claude.session import run_agent_once


def _is_transient_query_error(error: BaseException) -> bool:
    """从异常与已收集的 SDK 事件中识别只读定位请求的传输中断。"""
    message = str(error).casefold()
    events = getattr(error, "agent_events", [])
    # Claude SDK 有时只把 ResultMessage.result 设为 "success"，真正的
    # Connection closed mid-response 只出现在此前的 Assistant/Result 事件里。
    # 只看异常字符串会漏掉这类错误，随后被误报成 Agent 系统级崩溃。
    try:
        event_text = json.dumps(events, ensure_ascii=False, default=str).casefold()
    except Exception:
        event_text = str(events).casefold()
    diagnostic = message + "\\n" + event_text
    markers = (
        "connection closed mid-response", "connection reset", "server disconnected",
        "incomplete chunked read", "remoteprotocolerror", "readtimeout", "connecttimeout",
        "broken pipe", "temporarily unavailable", "connection aborted",
    )
    return any(marker in diagnostic for marker in markers) or isinstance(error, (TimeoutError, ConnectionError, OSError))


def _interrupted_call_fallback(error: BaseException, request: AgentRequest, attempts: int) -> dict[str, Any]:
    """只读定位重试耗尽后转为可审计的人工介入结果，不崩掉整轮任务。"""
    reason = (
        f"Claude 定位请求在 {attempts} 次尝试后仍然中断：{str(error).strip() or type(error).__name__}；"
        "没有获得可验证的完整定位结论，可重新定位或转人工处理。"
    )[:1800]
    payload = {
        "status": "MANUAL", "root_cause": "", "category": "unknown",
        "facts": [], "source_evidence": [], "candidate_files": [],
        "evidence_gaps": [reason], "recommended_scope": "manual", "summary": reason,
    }
    result = json.dumps(payload, ensure_ascii=False)
    messages = getattr(error, "agent_events", [])
    if not isinstance(messages, list):
        messages = []
    return {
        "passed": False, "reason": reason, "result": result, "messages": messages,
        "usage": {
            "tool_calls": sum(1 for event in messages if isinstance(event, dict) and event.get("type") == "tool_use"),
            "prompt_chars": len(request.system_prompt) + len(request.prompt),
            "result_chars": len(result), "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        },
    }

def _is_max_turns_error(error: BaseException) -> bool:
    """识别 Claude SDK 将回合耗尽作为异常抛出的不同文案。"""
    message = str(error).casefold()
    return "maximum number of turns" in message or "error_max_turns" in message


def _max_turns_fallback(
    error: BaseException, max_turns: int, *, prompt_chars: int = 0
) -> dict[str, Any]:
    """将回合耗尽转为可审计的证据不足结论，而不是系统级崩溃。"""
    reason = (
        f"Claude 定位 Agent 已达到最大交互回合数 {max_turns}，"
        "未能在预算内形成可验证结论；请检查事件记录，补充证据后重新执行。"
    )
    payload = {
        "status": "INSUFFICIENT_EVIDENCE",
        "root_cause": "",
        "category": "unknown",
        "facts": [],
        "source_evidence": [],
        "candidate_files": [],
        "evidence_gaps": [reason],
        "recommended_scope": "manual",
        "summary": reason,
    }
    messages = getattr(error, "agent_events", [])
    if not isinstance(messages, list):
        messages = []
    tool_calls = sum(
        1 for event in messages
        if isinstance(event, dict) and event.get("type") == "tool_use"
    )
    result = json.dumps(payload, ensure_ascii=False)
    return {
        "passed": False,
        "reason": reason,
        "result": result,
        "messages": messages,
        "usage": {
            "tool_calls": tool_calls,
            "prompt_chars": max(0, int(prompt_chars)),
            "result_chars": len(result),
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        },
    }


class ClaudeQueryLocateProvider:
    """执行一次定位请求，不保存跨任务会话状态。"""

    name = "claude_query"

    def __init__(self, config: LocateConfig, env: Mapping[str, str] | None = None) -> None:
        self.config = config
        self.env = dict(env or {})

    def run(self, request: AgentRequest) -> dict[str, Any]:
        ToolPolicy.validate(request)
        if request.agent_role != "locate":
            raise ValueError("Locate provider 只接受 locate 角色请求")
        if request.allowed_write_roots or request.write_root is not None:
            raise ValueError("Locate provider 拒绝任何写入权限")
        prompt = f"{request.system_prompt}\n\n---\n\n{request.prompt}"

        def invoke() -> dict[str, Any]:
            attempts = 3
            last_error: BaseException | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return run_agent_once(
                        prompt, request.workspace, self.env, writable=False, tools=self.config.tools,
                        max_turns=self.config.max_turns, max_tool_calls=self.config.max_tool_calls,
                        timeout_seconds=self.config.timeout_seconds, allowed_roots=request.allowed_read_roots,
                    )
                except Exception as error:
                    last_error = error
                    if _is_max_turns_error(error):
                        return _max_turns_fallback(error, self.config.max_turns, prompt_chars=len(prompt))
                    # 定位 Agent 只有 Read/Glob/Grep 权限，没有副作用；只对明确的传输中断自动重试。
                    if not _is_transient_query_error(error) or attempt >= attempts:
                        break
                    import time
                    time.sleep(0.25 * attempt)
            assert last_error is not None
            if _is_transient_query_error(last_error):
                return _interrupted_call_fallback(last_error, request, attempts)
            raise last_error

        return invoke_with_audit(request, invoke)


PROVIDERS: dict[str, type[ClaudeQueryLocateProvider]] = {
    ClaudeQueryLocateProvider.name: ClaudeQueryLocateProvider,
}


def create_provider(
    name: str,
    config: LocateConfig,
    env: Mapping[str, str] | None = None,
) -> ClaudeQueryLocateProvider:
    factory = PROVIDERS.get(name)
    if factory is None:
        raise ValueError(f"未注册的定位 agent provider: {name}")
    return factory(config, env)


__all__ = [
    "PROVIDERS",
    "ClaudeQueryLocateProvider",
    "create_provider",
    "_is_max_turns_error",
    "_max_turns_fallback",
    "_is_transient_query_error",
    "_interrupted_call_fallback",
]
