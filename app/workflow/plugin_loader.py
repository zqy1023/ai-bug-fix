"""发现并注册内置步骤与 Agent 子图。"""

from __future__ import annotations

import importlib
from types import ModuleType

from app.workflow.registry import PluginError, register
from app.workflow.spec import AgentSpec, PluginSpec, StepSpec

BUILTIN_STEPS = (
    "check_changes",
    "claim_repair_slot",
    "deduplicate_error",
    "discover_logs",
    "finalize_execution",
    "identify_carrier",
    "normalize_error",
    "prepare_delivery",
    "prepare_evidence",
    "prepare_source_snapshot",
    "prepare_workspace",
    "request_approval",
    "run_regression",
    "verify_issue",
)
BUILTIN_AGENTS = ("locate", "repair", "review")

_LOADED = False


def _spec_of(module: ModuleType) -> PluginSpec:
    """取插件包里的 ``SPEC``，类型不对或缺失都算契约错误。"""
    spec = getattr(module, "SPEC", None)
    if not isinstance(spec, (StepSpec, AgentSpec)):
        raise PluginError(f"插件包缺少合法 SPEC: {module.__name__}")
    return spec


def _load(package: str, name: str) -> PluginSpec:
    return register(_spec_of(importlib.import_module(f"{package}.{name}")))


def load_step(name: str) -> PluginSpec:
    """按需装载单个步骤，单步调试不导入无关插件。"""
    if name not in BUILTIN_STEPS:
        raise PluginError(f"未注册的内置步骤: {name}")
    return _load("app.workflow.steps", name)


def load_agent(name: str) -> PluginSpec:
    """按需装载单个 Agent 子图。

    接受短名（locate / repair / review）或全名（locate_agent / repair_agent / review_agent）。
    """
    # 支持两种名字：短名（BUILTIN_AGENTS）和全名（SPEC.name）。
    short_name = name.replace("_agent", "") if name.endswith("_agent") else name
    if short_name not in BUILTIN_AGENTS:
        raise PluginError(f"未注册的内置 Agent: {name}")
    return _load("app.agents", short_name)


def load_builtin() -> None:
    """装载主图所需的内置步骤和 Agent 子图。"""
    for name in BUILTIN_STEPS:
        load_step(name)
    for name in BUILTIN_AGENTS:
        load_agent(name)


def load_all() -> None:
    """装载内置步骤和 Agent。重复调用只生效一次。"""
    global _LOADED
    if _LOADED:
        return
    load_builtin()
    _LOADED = True


def reset() -> None:
    """允许重新装载。只给测试和回放用。"""
    global _LOADED
    _LOADED = False


__all__ = [
    "BUILTIN_AGENTS",
    "BUILTIN_STEPS",
    "load_all",
    "load_agent",
    "load_builtin",
    "load_step",
    "reset",
]
