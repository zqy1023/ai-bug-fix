"""工作流编排中枢引擎 (Workflow Engine)。

核心四大模块：
- graph.py   : 全图构建与编译 (LangGraph StateGraph, Checkpoint, Entrypoint, Dispatch)
- runner.py  : 步骤执行与审计记录调度 (StepRunner, Plugins, Registry, Services, Checks)
- routes.py  : 阶段流转条件分支判断 (Routers, Definitions, Validation, Acceptance)
- handoff.py : 状态快照与阶段数据移交 (State, Handoff, Task Execution, Retry Context)

历史上这里用 ``sys.modules`` 别名把 ``wiring``/``entrypoint``/``definitions`` 等
虚构模块名指向上面四个文件。别名让 import 语句看起来成立，但静态分析看不见、
报错位置指向别处、也让 ``core`` 反向依赖编排层。现在所有调用点已改为直接引用
真实模块，别名表已删除。
"""

from __future__ import annotations

from typing import Any

from workflow.engine import graph, handoff, routes, runner

# 导出核心模块
__all__ = ["graph", "handoff", "routes", "runner"]


def __getattr__(name: str) -> Any:
    """允许 ``from workflow.engine import build_graph`` 这类便捷访问。"""
    for mod in (graph, runner, routes, handoff):
        if hasattr(mod, name):
            return getattr(mod, name)
    raise AttributeError(f"module 'workflow.engine' has no attribute '{name}'")
