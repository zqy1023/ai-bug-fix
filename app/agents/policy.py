"""Agent 工具范围的确定性策略。"""

from __future__ import annotations

from pathlib import Path

from app.application.contracts import AgentRequest
from app.infrastructure.filesystem.workspace import WorkspaceManager

_SHARED_READ_DIRECTORIES = ("framework", "gathers_base", "gather_providers", "common")
_MAX_RULES_CHARS = 12000


def rules_text(*roots: Path) -> str:
    """读取项目规范正文；去重并限长，避免 Prompt 被文档占满。"""
    parts: list[str] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for relative in WorkspaceManager.readable_rule_paths(root):
            target = (root / relative).resolve()
            if target in seen or not target.is_file():
                continue
            seen.add(target)
            try:
                text = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            parts.append(f"# {relative}\n{text}")
    return "\n\n".join(parts)[:_MAX_RULES_CHARS]


def shared_read_paths(shared_root: Path | None) -> list[Path]:
    """共享只读树中允许 Agent 读取的目录、插件注册表和项目规范。

    顺序必须与 Prompt 里的 ``allowed_read_roots`` 完全一致；Policy 按元组逐项
    比对，顺序不同会误报权限范围不符。
    """
    if shared_root is None or not shared_root.is_dir():
        return []
    paths = [
        shared_root / name
        for name in _SHARED_READ_DIRECTORIES
        if (shared_root / name).is_dir()
    ]
    plugin_registry = shared_root / "plugins.py"
    if plugin_registry.is_file():
        paths.append(plugin_registry)
    paths.extend(
        shared_root / relative
        for relative in WorkspaceManager.readable_rule_paths(shared_root)
    )
    return paths


def _shared_root_of(context: dict[str, object]) -> Path | None:
    raw = context.get("source_project_path")
    if not isinstance(raw, str) or not raw.strip():
        return None
    root = Path(raw).resolve()
    return root if root.is_dir() else None


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
        carrier_path = WorkspaceManager.find_carrier_dir(workspace, str(framework), carrier)
        if carrier_path is None:
            raise ToolPolicyError(f"Agent 授权目标不存在: {framework}/{carrier}")
        expected_carrier = carrier_path.resolve()
        if not expected_carrier.is_relative_to(workspace):
            raise ToolPolicyError(f"Agent 授权目标不存在: {expected_carrier}")

        shared_root = _shared_root_of(context)
        rules = tuple(
            (workspace / relative).resolve()
            for relative in WorkspaceManager.readable_rule_paths(workspace)
        )
        if request.agent_role == "locate":
            expected_reads: tuple[Path, ...] = (expected_carrier,)
            # 新框架（gathers）任务允许只读核对旧实现目录，与 locate._read_roots
            # 的装配保持一致：旧目录存在且与目标 carrier 不同时才计入白名单。
            legacy_path = WorkspaceManager.find_carrier_dir(workspace, "spiders", carrier)
            if (
                framework == "gathers"
                and legacy_path is not None
                and legacy_path.resolve() != expected_carrier
            ):
                expected_reads += (legacy_path.resolve(),)
            expected_reads += tuple(path.resolve() for path in shared_read_paths(shared_root))
            expected_reads += rules
        elif request.agent_role == "repair":
            expected_reads = (
                expected_carrier,
                *(path.resolve() for path in shared_read_paths(shared_root)),
                *rules,
            )
        else:
            expected_reads = (expected_carrier,)
        expected_reads = tuple(dict.fromkeys(expected_reads))

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
