"""插件契约自检。

装配前把"散在各个 SPEC 里的约定"一次性查清：模型能解析、前置步骤存在、依赖
无环、组合成员都是已注册步骤。契约坏了就装配时报错，不等到跑到一半才崩。
"""

from __future__ import annotations

from app.workflow.registry import PluginError, resolve
from app.workflow.spec import AgentSpec, StepSpec
from pydantic import BaseModel

_GRAPH_TARGETS = {"task_dispatch", ""}
_AGENT_KINDS = {"locate", "repair", "review"}


def verify_registry() -> None:
    """校验全部已注册插件。任一条不满足直接抛 :class:`PluginError`。"""
    from app.workflow import registry

    problems: list[str] = []
    for spec in registry.steps():
        problems.extend(_check_step(spec))
    for spec in registry.agents():
        problems.extend(_check_agent(spec))
    problems.extend(_check_cycles())
    problems.extend(_check_task_topology())
    if problems:
        raise PluginError("插件契约自检失败:\n- " + "\n- ".join(problems))


def _known(name: str) -> bool:
    try:
        resolve(name)
    except PluginError:
        return False
    return True



def _check_task_topology() -> list[str]:
    """任务图拓扑必须引用已声明的节点，且所有任务插件必须在主流程中有位置。"""
    from app.workflow.definitions import (
        TASK_CONDITIONAL_TRANSITIONS,
        TASK_NODE_NAMES,
        TASK_STATIC_TRANSITIONS,
    )
    from langgraph.graph import END, START

    problems: list[str] = []
    declared = set(TASK_NODE_NAMES)
    from app.workflow import registry
    registered = {spec.name for spec in registry.steps() if spec.task_level}
    registered |= {spec.name for spec in registry.agents() if spec.task_level}
    # retry_prepare 是编排内置节点，不是业务插件。
    registered.add("retry_prepare")
    for name in sorted(registered - declared):
        problems.append(f"任务插件未纳入任务流程: {name}")
    for name in sorted(declared - registered):
        problems.append(f"任务流程声明了不存在的节点: {name}")

    edges: list[tuple[str, str]] = list(TASK_STATIC_TRANSITIONS)
    for source, _router, targets in TASK_CONDITIONAL_TRANSITIONS:
        edges.extend((source, target) for target in targets)
    for source, target in edges:
        if source not in {START, END} and source not in declared:
            problems.append(f"任务流程边的源节点未声明: {source}")
        if target not in {START, END} and target not in declared:
            problems.append(f"任务流程边的目标节点未声明: {target}")
    conditional_sources = [source for source, _router, _targets in TASK_CONDITIONAL_TRANSITIONS]
    if len(conditional_sources) != len(set(conditional_sources)):
        problems.append("任务流程同一节点声明了多组条件路由")
    return problems

def _check_models(spec: StepSpec | AgentSpec) -> list[str]:
    problems: list[str] = []
    for label, model in (("input_model", spec.input_model), ("output_model", spec.output_model)):
        if not isinstance(model, type) or not issubclass(model, BaseModel):
            problems.append(f"{spec.name}.{label} 不是 pydantic 模型")
    return problems


def _check_depends_on(spec: StepSpec | AgentSpec) -> list[str]:
    return [
        f"{spec.name}.depends_on 指向未注册插件: {dependency.name}"
        for dependency in spec.depends_on
        if not _known(dependency.name)
    ]


def _check_next_step(spec: StepSpec | AgentSpec) -> list[str]:
    """下一步必须指向注册插件或唯一任务分发节点。"""
    if spec.next_step in _GRAPH_TARGETS or _known(spec.next_step):
        return []
    return [f"{spec.name}.next_step 指向未注册插件: {spec.next_step}"]


def _check_step(spec: StepSpec) -> list[str]:
    if not callable(spec.runner):
        return [f"{spec.name}.runner 不可调用"]
    return _check_models(spec) + _check_depends_on(spec) + _check_next_step(spec)


def _check_agent(spec: AgentSpec) -> list[str]:
    problems = _check_models(spec) + _check_depends_on(spec) + _check_next_step(spec)
    if spec.kind not in _AGENT_KINDS:
        problems.append(f"{spec.name}.kind 没有对应子图实现: {spec.kind}")
    return problems


def _check_cycles() -> list[str]:
    """步骤前置依赖成环时装配层会静默死循环，这里提前报错。"""
    from app.workflow import registry

    graph: dict[str, list[str]] = {
        spec.name: [dependency.name for dependency in spec.depends_on]
        for spec in registry.steps()
    }
    visiting: set[str] = set()
    visited: set[str] = set()
    problems: list[str] = []

    def walk(name: str, trail: tuple[str, ...]) -> None:
        if name in visiting:
            problems.append("步骤依赖成环: " + " -> ".join((*trail, name)))
            return
        if name in visited:
            return
        visiting.add(name)
        for parent in graph.get(name, []):
            if parent in graph:
                walk(parent, (*trail, name))
        visiting.discard(name)
        visited.add(name)

    for name in graph:
        walk(name, ())
    return problems


__all__ = ["verify_registry"]
