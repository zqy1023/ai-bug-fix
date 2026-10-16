"""根因定位模型通道：仅提供有权限钩子的 Claude 一次性只读调用。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.agents.locate.config import LocateConfig
from app.agents.policy import ToolPolicy
from app.agents.common import invoke_with_audit
from app.application.contracts import AgentRequest
from app.infrastructure.claude.session import run_agent_once


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
        return invoke_with_audit(request, lambda: run_agent_once(
            prompt,
            request.workspace,
            self.env,
            writable=False,
            tools=self.config.tools,
            max_turns=self.config.max_turns,
            max_tool_calls=self.config.max_tool_calls,
            timeout_seconds=self.config.timeout_seconds,
            allowed_roots=request.allowed_read_roots,
        ))


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


__all__ = ["PROVIDERS", "ClaudeQueryLocateProvider", "create_provider"]
