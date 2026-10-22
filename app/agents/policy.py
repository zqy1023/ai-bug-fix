"""Agent 工具范围的确定性策略。"""

from __future__ import annotations

from pathlib import Path
import ast
import hashlib
import json

from app.application.contracts import AgentRequest
from app.infrastructure.filesystem.workspace import WorkspaceManager

_SHARED_READ_DIRECTORIES = ("framework", "gathers_base", "gather_providers", "common")
_MAX_RULES_CHARS = 12000


def retry_evidence_dir(root: Path, run_id: str, task_id: str) -> Path:
    """Return the system evidence directory owned by exactly one Run/Task."""
    identity = hashlib.sha256(f"{run_id}\x00{task_id}".encode()).hexdigest()
    return root / ".evidence" / "retry" / identity


def retry_read_paths(root: Path, run_id: str, task_id: str) -> list[Path]:
    """Authorize an existing, non-symlink evidence directory for its owner only."""
    directory = retry_evidence_dir(root, run_id, task_id)
    if not run_id or not task_id or not directory.is_dir() or directory.is_symlink():
        return []
    resolved = directory.resolve()
    if not resolved.is_relative_to(root.resolve()):
        return []
    try:
        owner = json.loads((directory / "owner.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [resolved] if owner == {"run_id": run_id, "task_id": task_id} else []


def carrier_write_paths(root: Path, carrier: str) -> list[Path]:
    """目标运输商的新旧实现均可写，其他运输商及共享层不在写白名单内。"""
    return [path.resolve() for framework in ("gathers", "spiders")
            if (path := WorkspaceManager.find_carrier_dir(root, framework, carrier)) is not None]


def carrier_read_paths(root: Path, primary: Path, shared: Path | None, carrier: str) -> list[Path]:
    """按显式 import 展开有限只读依赖；只扩展调查范围。"""
    # A gathers wrapper may dispatch through LegacySpiderProvider without an
    # explicit import in the carrier file; the same carrier's spiders tree is
    # therefore read-only evidence, never a write target.
    paths = [primary.resolve(), *carrier_write_paths(root, carrier), *shared_read_paths(shared)]
    legacy_root = WorkspaceManager.find_carrier_dir(root, "spiders", carrier)
    if legacy_root is not None:
        paths.append(legacy_root.resolve())
    pending = [file for base in carrier_write_paths(root, carrier) for file in base.rglob("*.py")]
    seen: set[Path] = set()
    while pending and len(seen) < 64:
        file = pending.pop(0)
        if file in seen or file.is_symlink():
            continue
        seen.add(file)
        try:
            tree = ast.parse(file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError):
            continue
        if shared is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
                continue
            parts = node.module.split(".")
            if len(parts) < 3 or parts[0] not in {"gathers", "spiders"}:
                continue
            target = shared.joinpath(*parts).with_suffix(".py")
            if target.is_file() and not any(parent.is_symlink() for parent in [target, *target.parents] if parent != shared.parent) and target.resolve().is_relative_to(shared.resolve()):
                paths.append(target.resolve())
                pending.append(target)
    paths.extend(root / item for item in WorkspaceManager.readable_rule_paths(root))
    return list(dict.fromkeys(path.resolve() for path in paths))


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
        if request.agent_role in {"locate", "repair"}:
            expected_reads: tuple[Path, ...] = tuple(carrier_read_paths(workspace, expected_carrier, shared_root, carrier))
        else:
            expected_reads = tuple(dict.fromkeys([expected_carrier, *carrier_write_paths(workspace, carrier)]))
        expected_reads = (*expected_reads, *retry_read_paths(
            workspace, str(request.run_id), str(request.task_id),
        ))
        expected_reads = tuple(dict.fromkeys(expected_reads))

        actual_reads = tuple(Path(value).resolve() for value in request.allowed_read_roots)
        if len(actual_reads) != len(set(actual_reads)) or actual_reads != expected_reads:
            raise ToolPolicyError(
                f"{request.agent_role} Agent 读取范围不符合角色白名单"
            )
        if request.agent_role == "repair":
            if not request.allowed_write_roots or not request.write_root:
                raise ToolPolicyError("repair Agent 必须指定运输商写入根")
            write_root = Path(request.write_root).resolve()
            allowed = tuple(Path(item).resolve() for item in request.allowed_write_roots)
            if write_root != expected_carrier or allowed != tuple(carrier_write_paths(workspace, carrier)):
                raise ToolPolicyError("repair Agent 只能写入当前任务的 carrier 目录")
        elif request.allowed_write_roots or request.write_root is not None:
            raise ToolPolicyError(f"{request.agent_role} Agent 不允许写入")


__all__ = ["ToolPolicy", "ToolPolicyError"]
