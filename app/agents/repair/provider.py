"""修复 agent 的模型通道（provider）。

本文件只服务修复 agent，不引用其它 agent 的任何实现。可用通道：

===================  ==========================================================
``claude_session``   常驻 SDK 会话，一次执行内复用，最快
``claude_query``     一次性 SDK 查询，任务间完全隔离
``manual``           不调用模型，直接转人工
===================  ==========================================================
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from app.infrastructure.claude.session import ClaudeSession, run_agent_once
from app.agents.policy import ToolPolicy
from app.agents.common import invoke_with_audit
from app.agents.repair.config import RepairConfig
from app.application.contracts import AgentRequest


class RepairProvider(ABC):
    """修复 agent 的通道基类。"""

    name: str = ""

    def __init__(self, config: RepairConfig, env: Mapping[str, str] | None = None) -> None:
        """注入独立配置和 SDK 环境变量。"""
        self.config = config
        self.env = dict(env or {})

    @abstractmethod
    def run(self, request: AgentRequest) -> dict[str, Any]:
        """执行一次调用，返回提供方原始结果。"""

    def close(self) -> None:  # noqa: B027 - close 是可选钩子，不是必须实现的抽象方法
        """释放通道资源；无状态通道无需覆写。"""


class ClaudeSessionProvider(RepairProvider):
    """常驻会话：按工作区复用，避免每个任务重新加载项目上下文。"""

    name = "claude_session"

    def __init__(self, config: RepairConfig, env: Mapping[str, str] | None = None) -> None:
        """初始化会话缓存。"""
        super().__init__(config, env)

    def run(self, request: AgentRequest) -> dict[str, Any]:
        """在常驻会话中提交任务提示词。同一工作区跨步骤复用。"""
        ToolPolicy.validate(request)
        from app.infrastructure.runtime import drop_session, shared_session

        session_key = (
            f"{request.workspace}\x00{request.run_id}\x00{request.task_id}"
            f"\x00{request.prompt_hash}"
        )
        session = shared_session(
            request.agent_role,
            session_key,
            lambda: ClaudeSession(
                request.workspace,
                self.env,
                request.system_prompt,
                tools=self.config.tools or None,
                writable=self.config.writable,
                max_turns=self.config.max_turns,
                max_tool_calls=self.config.max_tool_calls,
                timeout_seconds=self.config.timeout_seconds,
                allowed_roots=request.allowed_read_roots,
            ),
        )
        result = invoke_with_audit(request, lambda: session.invoke(
            request.prompt,
            request.write_root,
            allowed_roots=request.allowed_read_roots,
        ))
        if result.get("reason") == "AGENT_TIMEOUT":
            drop_session(request.agent_role, session_key)
        return result

    def close(self) -> None:
        """会话由进程级缓存持有，这里不关闭。"""


class ClaudeQueryProvider(RepairProvider):
    """一次性查询：每次调用新建 SDK 会话，隔离性更好但更慢。"""

    name = "claude_query"

    def run(self, request: AgentRequest) -> dict[str, Any]:
        """以一次性查询方式调用智能体。"""
        try:
            ToolPolicy.validate(request)
            if request.write_root is None:
                raise ValueError("repair Agent 请求没有 write_root")
            env = {**self.env, "AI_REPAIR_WRITE_ROOT": request.write_root}
            prompt = f"{request.system_prompt}\n\n---\n\n{request.prompt}"
            return invoke_with_audit(request, lambda: run_agent_once(
                prompt,
                request.workspace,
                env,
                writable=self.config.writable,
                tools=self.config.tools or None,
                max_turns=self.config.max_turns,
                max_tool_calls=self.config.max_tool_calls,
                timeout_seconds=self.config.timeout_seconds,
                allowed_roots=request.allowed_read_roots,
            ))
        except TimeoutError:
            return {"passed": False, "reason": "AGENT_TIMEOUT"}


class ManualProvider(RepairProvider):
    """不调用模型：显式声明需要人工修复。"""

    name = "manual"
    _REASON = "provider=manual，修复步骤需要人工处理"

    def run(self, request: AgentRequest) -> dict[str, Any]:
        """返回明确的人工处理结果，不伪造模型输出。"""
        ToolPolicy.validate(request)
        payload = {"passed": False, "result": "MANUAL", "reason": self._REASON}
        return {
            "passed": False,
            "manual": True,
            "reason": self._REASON,
            "result": json.dumps(payload, ensure_ascii=False),
        }


PROVIDERS: dict[str, type[RepairProvider]] = {
    ClaudeSessionProvider.name: ClaudeSessionProvider,
    ClaudeQueryProvider.name: ClaudeQueryProvider,
    ManualProvider.name: ManualProvider,
}


def create_provider(
    name: str,
    config: RepairConfig,
    env: Mapping[str, str] | None = None,
) -> RepairProvider:
    """按名字创建通道，未注册时列出可用通道。"""
    factory = PROVIDERS.get(name)
    if factory is None:
        available = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"未注册的修复 agent provider: {name}（可用: {available}）")
    return factory(config, env)


__all__ = [
    "PROVIDERS",
    "ClaudeQueryProvider",
    "ClaudeSessionProvider",
    "ManualProvider",
    "RepairProvider",
    "create_provider",
]
