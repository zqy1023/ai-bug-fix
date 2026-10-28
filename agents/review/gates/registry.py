"""审查 Agent 内部工具目录，不参与主工作流插件注册。"""
from __future__ import annotations

import importlib

from core.contracts import StepSpec

TOOL_NAMES = (
    "check_changes", "syntax_regression", "plan_validation",
    "verify_issue", "run_regression", "summarize_validation",
)


def resolve_tool(name: str) -> StepSpec:
    """仅允许审查 Agent 调用固定的内部工具。"""
    if name not in TOOL_NAMES:
        raise ValueError(f"未知审查工具: {name}")
    return importlib.import_module(f"agents.review.gates.{name}").SPEC
