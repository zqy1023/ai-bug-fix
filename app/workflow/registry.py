"""插件注册表。

装配层和单步调试都从这里按名字取插件，不再逐个 import 具体实现。
名字就是插件名，也就是目录名和步骤记录名：只有一套命名，不做别名映射。
"""

from __future__ import annotations

from app.workflow.spec import AgentSpec, PluginSpec, StepSpec


class PluginError(RuntimeError):
    """插件契约不完整、名字冲突或未注册。"""


_REGISTRY: dict[str, PluginSpec] = {}


def register(spec: PluginSpec) -> PluginSpec:
    """登记一个插件。同名重复登记只在是同一个对象时放行，否则报错。"""
    existing = _REGISTRY.get(spec.name)
    if existing is not None and existing is not spec:
        raise PluginError(f"插件名冲突: {spec.name}")
    _REGISTRY[spec.name] = spec
    return spec


def resolve(name: str) -> PluginSpec:
    """按插件名取契约。取不到就报错，不做兜底。"""
    spec = _REGISTRY.get(name)
    if spec is None:
        raise PluginError(f"未注册的插件: {name}")
    return spec


def steps() -> tuple[StepSpec, ...]:
    """全部步骤插件，按注册名排序。"""
    found = (item for item in _REGISTRY.values() if isinstance(item, StepSpec))
    return tuple(sorted(found, key=lambda item: item.name))


def agents() -> tuple[AgentSpec, ...]:
    """全部 agent 插件，按注册名排序。"""
    found = (item for item in _REGISTRY.values() if isinstance(item, AgentSpec))
    return tuple(sorted(found, key=lambda item: item.name))


def names() -> tuple[str, ...]:
    """已注册的插件名。"""
    return tuple(sorted(_REGISTRY))


def clear() -> None:
    """清空注册表。只给测试和回放用。"""
    _REGISTRY.clear()


__all__ = [
    "PluginError",
    "agents",
    "clear",
    "names",
    "register",
    "resolve",
    "steps",
]
