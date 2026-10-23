"""所有 Agent 共用的 Prompt 渲染器。"""

from __future__ import annotations

import hashlib
import json

from app.application.contracts import AgentRequest, AgentRole
from app.prompts.loader import PromptLoader
from app.prompts.models import PromptContext, RenderedPrompt


class PromptRenderer:
    """以相同顺序组合公共规则、角色职责和结构化任务上下文。"""

    def __init__(self, loader: PromptLoader | None = None) -> None:
        self._loader = loader or PromptLoader()

    def render(self, role: AgentRole, context: PromptContext) -> RenderedPrompt:
        """渲染系统规则和本次任务数据，并计算版本与内容哈希。"""
        if context.agent_role != role:
            raise ValueError(f"PromptContext 角色不匹配: {context.agent_role} != {role}")
        common, specific = self._loader.load(role)
        system_prompt = f"{common.body}\n\n---\n\n{specific.body}"
        payload = context.model_dump(mode="json", exclude={"agent_role"})
        prompt = "## 本次任务上下文\n\n```json\n"
        prompt += json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        prompt += "\n```"
        version = f"common/{common.version}+{role}/{specific.version}"
        digest = hashlib.sha256(
            f"{version}\0{system_prompt}\0{prompt}".encode("utf-8")
        ).hexdigest()
        return RenderedPrompt(
            system_prompt=system_prompt,
            prompt=prompt,
            prompt_version=version,
            prompt_hash=digest,
        )

    def request(
        self,
        role: AgentRole,
        context: PromptContext,
        *,
        stage: str,
        workspace: str,
        model: str,
        write_root: str | None = None,
    ) -> AgentRequest:
        """将渲染结果和运行权限组合成 Provider 的唯一请求契约。"""
        from app.prompts.budget import bounded_context
        context = bounded_context(context, workspace)
        rendered = self.render(role, context)
        return AgentRequest(
            agent_role=role,
            run_id=context.run_id,
            task_id=context.task_id,
            attempt=context.attempt,
            prompt_context=context.model_dump(mode="json"),
            input_evidence=context.evidence,
            budget=context.budget,
            stage=stage,
            workspace=workspace,
            system_prompt=rendered.system_prompt,
            prompt=rendered.prompt,
            prompt_version=rendered.prompt_version,
            prompt_hash=rendered.prompt_hash,
            logical_context_hash=rendered.prompt_hash,
            model=model,
            allowed_read_roots=context.allowed_read_roots,
            allowed_write_roots=context.allowed_write_roots,
            write_root=write_root,
        )


__all__ = ["PromptRenderer"]
