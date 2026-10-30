"""工作流编排中枢引擎 (Workflow Engine)。

核心四大中枢架构：
- graph.py   : 全图构建与编译 (LangGraph StateGraph, Checkpoint, Entrypoint, Dispatch)
- runner.py  : 步骤执行与审计记录调度 (StepRunner, Plugins, Registry, Services, Checks)
- routes.py  : 阶段流转条件分支判断 (Routers, Definitions, Validation, Acceptance)
- handoff.py : 状态快照与阶段数据移交 (State, Handoff, Task Execution, Retry Context)
"""

from __future__ import annotations

import sys
from typing import Any

from workflow.engine import graph, handoff, routes, runner

# 导出核心模块
__all__ = ["graph", "handoff", "routes", "runner"]

# 历史模块透明别名映射表
_LEGACY_MODULE_MAP = {
    # routes 承载
    "workflow.engine.definitions": routes,
    "workflow.engine.confirmation": routes,
    # graph 承载
    "workflow.engine.wiring": graph,
    "workflow.engine.entrypoint": graph,
    # runner 承载
    "workflow.engine.step_runner": runner,
    "workflow.engine.loader": runner,
    "workflow.engine.registry": runner,
    # handoff 承载
    "workflow.engine.retry_context": handoff,
    "workflow.engine.evidence_version": handoff,
    "workflow.engine.frontend_contracts": handoff,
}

for legacy_name, target_mod in _LEGACY_MODULE_MAP.items():
    sys.modules[legacy_name] = target_mod


def __getattr__(name: str) -> Any:
    full_name = f"workflow.engine.{name}"
    if full_name in _LEGACY_MODULE_MAP:
        return _LEGACY_MODULE_MAP[full_name]
    for mod in (graph, runner, routes, handoff):
        if hasattr(mod, name):
            return getattr(mod, name)
    raise AttributeError(f"module 'workflow.engine' has no attribute '{name}'")
