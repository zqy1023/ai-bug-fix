"""审查 agent 的模型通道（provider）。

本文件只服务审查 agent，不引用其它 agent 的任何实现。所有通道都只读：

===================  ==========================================================
``claude_session``   常驻 SDK 会话（只读工具集）
``claude_query``     一次性 SDK 查询（只读）
``manual``           不调用模型，按转人工处理
===================  ==========================================================
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from app.infrastructure.claude.session import ClaudeSession, resolve_tools, run_agent_once
from app.agents.policy import ToolPolicy
from app.agents.common import invoke_with_audit
from app.agents.review.config import ReviewConfig
from app.application.contracts import AgentRequest


class ReviewProvider(ABC):
    """审查 agent 的通道基类。"""

    name: str = ""

    def __init__(self, config: ReviewConfig, env: Mapping[str, str] | None = None) -> None:
        """注入独立配置和 SDK 环境变量。"""
        self.config = config
        self.env = dict(env or {})
        self.tools = resolve_tools(writable=False, tools=self.config.tools or None)

    @abstractmethod
    def run(self, request: AgentRequest) -> dict[str, Any]:
        """执行一次调用，返回提供方原始结果。"""

    def close(self) -> None:  # noqa: B027 - close 是可选钩子，不是必须实现的抽象方法
        """释放通道资源；无状态通道无需覆写。"""


class ClaudeSessionProvider(ReviewProvider):
    """常驻只读会话：按任务隔离，避免共享工作区串上下文。"""

    name = "claude_session"

    def __init__(self, config: ReviewConfig, env: Mapping[str, str] | None = None) -> None:
        """初始化会话缓存。"""
        super().__init__(config, env)

    def run(self, request: AgentRequest) -> dict[str, Any]:
        """在常驻只读会话中提交提示词；同一任务跨回修轮次复用。"""
        from app.agents.output_schema import output_schema
        request = request.model_copy(update={"provider": self.name})
        ToolPolicy.validate(request)
        from app.infrastructure.runtime import drop_session, session_scope
        from app.agents.session_context import invoke_session, session_fingerprint

        # key 不含 prompt_hash：每轮提示词必然不同，带它进来会让缓存永不命中。
        session_key = f"{request.workspace}\x00{request.run_id}\x00{request.task_id}"
        schema = output_schema("review")
        fingerprint = session_fingerprint(request, schema, self.config.to_dict(), self.env)
        with session_scope(
            request.agent_role,
            # 一个 workspace 可能承载多个 carrier/task；按 task 隔离即可在回修轮次复用
            # 已加载的只读上下文，同时避免跨任务污染。
            session_key,
            lambda: ClaudeSession(
                request.workspace,
                self.env,
                request.system_prompt,
                output_schema=schema,
                tools=self.tools,
                writable=False,
                max_turns=self.config.max_turns,
                max_tool_calls=self.config.max_tool_calls,
                timeout_seconds=self.config.timeout_seconds,
            ),
            run_id=request.run_id,
            task_id=request.task_id,
            fingerprint=fingerprint,
        ) as session:
            result = invoke_session(request, session, lambda sent: invoke_with_audit(sent, lambda: session.invoke(
                sent.prompt,
                request.workspace,
                allowed_roots=request.allowed_read_roots,
            )))
            if result.get("reason"):
                drop_session(request.agent_role, session_key)
        return result

    def close(self) -> None:
        """会话由进程级缓存持有，这里不关闭。"""


class ClaudeQueryProvider(ReviewProvider):
    """一次性只读查询：任务间完全隔离。"""

    name = "claude_query"

    def run(self, request: AgentRequest) -> dict[str, Any]:
        """以一次性查询方式调用只读智能体。"""
        from app.agents.output_schema import output_schema
        try:
            request = request.model_copy(update={"provider": self.name})
            ToolPolicy.validate(request)
            return invoke_with_audit(request, lambda: run_agent_once(
                f"{request.system_prompt}\n\n---\n\n{request.prompt}",
                request.workspace,
                self.env,
                writable=False,
                tools=self.tools,
                max_turns=self.config.max_turns,
                max_tool_calls=self.config.max_tool_calls,
                timeout_seconds=self.config.timeout_seconds,
                allowed_roots=request.allowed_read_roots,
                output_schema=output_schema("review"),
            ))
        except TimeoutError:
            return {"passed": False, "reason": "AGENT_TIMEOUT"}


class ManualProvider(ReviewProvider):
    """不调用模型：按转人工审查处理。"""

    name = "manual"
    _REASON = "provider=manual，审查转人工执行"

    def run(self, request: AgentRequest) -> dict[str, Any]:
        """返回 MANUAL 结论，交由人工审批。"""
        request = request.model_copy(update={"provider": self.name})
        ToolPolicy.validate(request)
        payload = {
            "result": "MANUAL",
            "passed": False,
            "code_verdict": "UNKNOWN",
            "coverage": {"decision": "unknown", "reason": "人工审查模式，尚未评估本次变更覆盖",
                         "reviewed_functions": [], "missing_checks": ["人工核对源码及验证证据"]},
            "mode": "MANUAL_REVIEW_REQUIRED",
            "summary": self._REASON,
            "findings": [],
            "required_changes": [],
        }
        return {
            "passed": True,
            "manual": True,
            "reason": self._REASON,
            "result": json.dumps(payload, ensure_ascii=False),
        }


PROVIDERS: dict[str, type[ReviewProvider]] = {
    ClaudeSessionProvider.name: ClaudeSessionProvider,
    ClaudeQueryProvider.name: ClaudeQueryProvider,
    ManualProvider.name: ManualProvider,
}


def create_provider(
    name: str,
    config: ReviewConfig,
    env: Mapping[str, str] | None = None,
) -> ReviewProvider:
    """按名字创建通道，未注册时列出可用通道。"""
    factory = PROVIDERS.get(name)
    if factory is None:
        available = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"未注册的审查 agent provider: {name}（可用: {available}）")
    return factory(config, env)


__all__ = [
    "PROVIDERS",
    "ClaudeQueryProvider",
    "ClaudeSessionProvider",
    "ManualProvider",
    "ReviewProvider",
    "create_provider",
]
