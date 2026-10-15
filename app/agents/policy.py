"""Agent 工具范围的确定性策略。"""

from __future__ import annotations

from pathlib import Path

from app.application.contracts import AgentRequest
from app.infrastructure.filesystem.workspace import WorkspaceManager

_SHARED_READ_DIRECTORIES = ("framework", "gathers_base", "gather_providers", "common")


class ToolPolicyError(ValueError):
    """Agent 请求试图访问声明范围之外的文件。"""


class ToolPolicy:
    """在 provider 调用前校验角色专属的精确读写白名单。"""

    @staticmethod
    def validate(request: AgentRequest) -> None:
        workspace = Path(request.workspace).resolve()
        if not workspace.is_dir():
            raise ToolPolicyError(f"Agent 工作区不存在: {workspace}")
        context = request.prompt_context
        carrier = context.get("carrier")
        framework = context.get("framework")
        if not isinstance(carrier, str) or not carrier or framework not in {"gathers", "spiders"}:
            raise ToolPolicyError("AgentRequest 缺少可信 carrier/framework 范围")
        carrier_path = workspace / str(framework) / WorkspaceManager.safe_carrier_name(carrier)
        expected_carrier = carrier_path.resolve()
        if not expected_carrier.is_relative_to(workspace) or not carrier_path.is_dir():
            raise ToolPolicyError(f"Agent 授权目标不存在: {expected_carrier}")

        rules = tuple(
            (workspace / relative).resolve()
            for relative in WorkspaceManager.readable_rule_paths(workspace)
        )
        if request.agent_role == "locate":
            expected_reads = (expected_carrier, *(
                (workspace / name).resolve()
                for name in _SHARED_READ_DIRECTORIES
                if (workspace / name).is_dir()
            ))
            plugin_registry = workspace / "plugins.py"
            if plugin_registry.is_file():
                expected_reads += (plugin_registry.resolve(),)
            expected_reads += rules
        elif request.agent_role == "repair":
            expected_reads = (expected_carrier, *rules)
        else:
            expected_reads = (expected_carrier,)

        actual_reads = tuple(Path(value).resolve() for value in request.allowed_read_roots)
        if len(actual_reads) != len(set(actual_reads)) or actual_reads != expected_reads:
            raise ToolPolicyError(
                f"{request.agent_role} Agent 读取范围不符合角色白名单"
            )
        if request.agent_role == "repair":
            if len(request.allowed_write_roots) != 1 or not request.write_root:
                raise ToolPolicyError("repair Agent 必须指定唯一写入根")
            write_root = Path(request.write_root).resolve()
            allowed_root = Path(request.allowed_write_roots[0]).resolve()
            if write_root != expected_carrier or allowed_root != expected_carrier:
                raise ToolPolicyError("repair Agent 只能写入当前任务的 carrier 目录")
        elif request.allowed_write_roots or request.write_root is not None:
            raise ToolPolicyError(f"{request.agent_role} Agent 不允许写入")


__all__ = ["ToolPolicy", "ToolPolicyError"]
