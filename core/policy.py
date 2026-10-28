"""统一执行策略、白名单路径、模型分配与范围门禁（系统唯一策略源）。"""

from __future__ import annotations

import ast
import functools
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.contracts import AgentRequest





def legacy_analysis_required(state: dict[str, Any]) -> bool:
    """依据确定性预检及明确的修改目标保留旧框架只读边界。"""
    evidence = state.get("evidence") or {}
    framework = evidence.get("framework") or state.get("framework")
    targets = [
        *(state.get("changed_files") or []),
        *((state.get("locate") or {}).get("proposed_edit_files") or []),
    ]
    return (
        framework == "spiders"
        or evidence.get("legacy_only") is True
        or evidence.get("legacy_dependency") is True
        or any(
            str(path).replace("\\", "/").casefold().startswith("spiders/")
            for path in targets
        )
    )






LocateDisposition = Literal["repair", "manual"]

RetryTarget = Literal[
    "unified_fixer_agent",
    "review_agent",
    "generate_report",
]

APPROVAL_RETRY_TARGETS = frozenset(get_args(RetryTarget))


def locate_disposition(status: str) -> LocateDisposition:
    return "repair" if status == "LOCATED" else "manual"





import ast
import functools
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath



_SHARED_READ_DIRECTORIES = ("framework", "gathers_base", "gather_providers", "common")
_MAX_RULES_CHARS = 12000
_INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_carrier_name(value: str) -> str:
    cleaned = _INVALID_PATH_CHARS.sub("-", str(value)).strip("-.")
    return cleaned or "carrier"


def find_child_dir(parent: Path, name: str) -> Path | None:
    if not parent.is_dir():
        return None
    direct = parent / name
    if direct.is_dir():
        return direct
    target_folded = name.casefold()
    try:
        for entry in os.scandir(parent):
            if entry.is_dir() and entry.name.casefold() == target_folded:
                return Path(entry.path)
    except OSError:
        return None
    return None


def find_carrier_dir(root: str | Path, framework: str, carrier: str) -> Path | None:
    return find_child_dir(Path(root) / framework, safe_carrier_name(carrier))


def readable_rule_paths(root: Path) -> list[str]:
    paths: list[str] = []
    for relative in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
        if (root / relative).is_file():
            paths.append(relative)
    rules_dir = root / ".rules"
    if rules_dir.is_dir():
        for path in sorted(rules_dir.rglob("*.md")):
            if path.is_file():
                try:
                    paths.append(str(path.relative_to(root)).replace("\\", "/"))
                except ValueError:
                    pass
    return paths


class AgentPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    unified_model: str = ""
    review_model: str = ""
    analyze_legacy: bool = True
    reproduce: bool = True
    acceptance: bool = True
    repair_legacy: bool = False
    repair_shared: bool = False
    shared_paths: list[str] = Field(default_factory=list)
    allow_new_files: bool = False
    allow_code_retirement: bool = False
    verify_fixed: bool = True
    request_regression: bool = True
    automatic_repair: bool = True
    max_repairs: int = Field(default=3, ge=1, le=10)
    local_commit: bool = True
    remote_push: bool = True

    def model_choice(self, role: Literal["unified_fixer", "review"]) -> str:
        return {
            "unified_fixer": self.unified_model,
            "review": self.review_model,
        }[role]

    @field_validator("shared_paths")
    @classmethod
    def safe_paths(cls, values: list[str]) -> list[str]:
        allowed = {"common", "framework", "gathers_base", "gather_providers"}
        for value in values:
            path = PurePosixPath(value)
            if "\\" in value or path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] not in allowed:
                raise ValueError("共享写入路径必须位于明确的 common/framework/gathers_base/gather_providers 子目录")
        return sorted(set(values))


def resolve_policy(batch: dict[str, Any] | None = None, task: dict[str, Any] | None = None) -> dict[str, Any]:
    from config.nacos import nacos_get
    system = json.loads(nacos_get("AI_REPAIR_AGENT_POLICY", "{}"))
    raw = {**system, **(batch or {}), **(task or {})}
    return AgentPolicy.model_validate(raw).model_dump(mode="json")


def policy_of(state: dict[str, Any]) -> AgentPolicy:
    raw = dict(state.get("agent_policy") or (state.get("evidence") or {}).get("agent_policy") or {})
    return AgentPolicy.model_validate(raw)


def skipped(reason: str = "用户关闭") -> dict[str, str]:
    return {"status": "skipped", "reason_code": "policy_disabled", "reason": reason}


def retry_evidence_dir(root: Path, run_id: str, task_id: str) -> Path:
    identity = hashlib.sha256(f"{run_id}\x00{task_id}".encode()).hexdigest()
    return root / ".evidence" / "retry" / identity


def retry_read_paths(root: Path, run_id: str, task_id: str) -> list[Path]:
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


def carrier_write_paths(root: Path, carrier: str, policy: Any = None) -> list[Path]:
    paths = [
        path.resolve()
        for framework in ("gathers",)
        if (path := find_carrier_dir(root, framework, carrier)) is not None
    ]
    if isinstance(policy, dict):
        shared_list = policy.get("shared_paths") or []
        for p in shared_list:
            target = (root / p).resolve()
            if target.is_dir() and target.is_relative_to(root.resolve()):
                paths.append(target)
    return paths


def shared_read_paths(shared_root: Path | None) -> list[Path]:
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
        for relative in readable_rule_paths(shared_root)
    )
    return paths


def carrier_read_paths(root: Path, primary: Path, shared: Path | None, carrier: str) -> list[Path]:
    paths = [primary.resolve(), *carrier_write_paths(root, carrier), *shared_read_paths(shared)]
    legacy_root = find_carrier_dir(root, "spiders", carrier)
    if legacy_root is not None:
        paths.append(legacy_root.resolve())
    source_roots = [*carrier_write_paths(root, carrier), *([legacy_root] if legacy_root is not None else [])]
    pending = [file for base in source_roots for file in base.rglob("*.py")]
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
    paths.extend(root / item for item in readable_rule_paths(root))
    return list(dict.fromkeys(path.resolve() for path in paths))


def review_read_paths(root: Path, primary: Path, shared: Path | None, carrier: str, baseline: str = "") -> list[Path]:
    paths = [primary.resolve(), *carrier_write_paths(root, carrier), *shared_read_paths(shared)]
    if baseline:
        base = Path(baseline).resolve()
        for framework in ("gathers", "spiders"):
            directory = find_carrier_dir(base, framework, carrier)
            if directory is not None:
                paths.append(directory.resolve())
    return list(dict.fromkeys(paths))


def rules_text(*roots: Path) -> str:
    parts: list[str] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for relative in readable_rule_paths(root):
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
        carrier_path = find_carrier_dir(workspace, str(framework), carrier)
        if carrier_path is None:
            raise ToolPolicyError(f"Agent 授权目标不存在: {framework}/{carrier}")
        expected_carrier = carrier_path.resolve()
        if not expected_carrier.is_relative_to(workspace):
            raise ToolPolicyError(f"Agent 授权目标不存在: {expected_carrier}")

        raw_shared = context.get("source_project_path")
        shared_root = Path(raw_shared).resolve() if isinstance(raw_shared, str) and raw_shared.strip() and Path(raw_shared).is_dir() else None
        if request.agent_role == "unified_fixer":
            expected_reads = tuple(carrier_read_paths(workspace, expected_carrier, shared_root, carrier))
        else:
            snapshot = context.get("source_snapshot") or {}
            expected_reads = tuple(review_read_paths(workspace, expected_carrier, shared_root, carrier, str(snapshot.get("baseline_dir") or "")))
        expected_reads = (*expected_reads, *retry_read_paths(workspace, str(request.run_id), str(request.task_id)))
        expected_reads = tuple(dict.fromkeys(expected_reads))
        from infrastructure.llm.budget import readable_context
        expected_reads = (*expected_reads, *readable_context(workspace, request.run_id, request.task_id, request.agent_role))

        actual_reads = tuple(Path(value).resolve() for value in request.allowed_read_roots)
        if len(actual_reads) != len(set(actual_reads)) or actual_reads != expected_reads:
            raise ToolPolicyError(f"{request.agent_role} Agent 读取范围不符合角色白名单")
        if request.agent_role == "unified_fixer":
            if request.allowed_write_roots or request.write_root:
                if legacy_analysis_required(context):
                    raise ToolPolicyError("旧框架及依赖旧插件的任务只允许分析报告，不允许修复写入")
                write_root = Path(request.write_root).resolve()
                allowed = tuple(Path(item).resolve() for item in request.allowed_write_roots)
                if write_root != expected_carrier or allowed != tuple(carrier_write_paths(workspace, carrier)):
                    raise ToolPolicyError("unified_fixer Agent 只能写入当前任务的 carrier 目录")
        elif request.allowed_write_roots or request.write_root is not None:
            raise ToolPolicyError(f"{request.agent_role} Agent 不允许写入")


class ServiceRegistryError(ValueError):
    """服务注册配置无法读取或校验。"""


@functools.lru_cache(maxsize=1)
def registered_services() -> frozenset[str]:
    """读取并校验 config/services/*.json 服务登记表。"""
    directory = Path(__file__).resolve().parents[1] / "config" / "services"
    manifests = sorted(directory.glob("*.json"))
    if not manifests:
        raise ServiceRegistryError(f"服务注册目录没有 JSON 配置: {directory}")
    services: list[str] = []
    for manifest in manifests:
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ServiceRegistryError(f"服务配置读取失败: {manifest}") from error
        service_name = payload.get("service_name") if isinstance(payload, dict) else None
        if not isinstance(service_name, str) or not service_name.strip():
            raise ServiceRegistryError(f"服务配置必须包含非空 service_name: {manifest}")
        services.append(service_name)
    if len(services) != len(set(services)):
        raise ServiceRegistryError(f"服务配置存在重复 service_name: {directory}")
    return frozenset(services)


def is_registered_service(service_name: str) -> bool:
    """判断 ES 原始服务名是否被当前工作流注册。"""
    if not service_name:
        return False
    return service_name in registered_services()


