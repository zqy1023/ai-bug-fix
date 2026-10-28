"""阶段 3 执行入口节点。"""

from __future__ import annotations

from typing import Any

from agents.unified_fixer.graph import run_unified_fixer


def run(state: dict[str, Any], runtime: Any = None) -> dict[str, Any]:
    """执行统一分析修复流程。"""
    return run_unified_fixer(state, runtime)


__all__ = ["run"]
