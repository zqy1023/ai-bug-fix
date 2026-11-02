"""步骤依赖拓扑（核心层静态数据，无外部依赖）。

放在 ``core`` 而不是 ``workflow.engine.routes``，是因为
``core.contracts.StepSpec.depends_on`` / ``AgentSpec.depends_on`` 需要它，而核心层
不能反向依赖编排层。此前靠 ``workflow.engine.__init__`` 里的 ``sys.modules``
别名伪造 ``workflow.engine.definitions`` 模块，静态分析看不见真实依赖，
单独 import ``core.contracts`` 的上下文一碰属性就 ImportError。
"""

from __future__ import annotations

from core.contracts import Dependency

# 每个步骤依赖哪些前置步骤；第二项为依赖层级（run / task）。
STEP_DEPENDENCIES: dict[str, list[tuple[str, str]]] = {
    "claim_repair_slot": [
        ("prepare_source_snapshot", "task"),
        ("prepare_evidence", "task"),
        ("prepare_workspace", "task"),
    ],
    "unified_fixer_agent": [("claim_repair_slot", "task")],
    "review_agent": [("unified_fixer_agent", "task")],
    "generate_report": [("unified_fixer_agent", "task")],
    "submit_changes": [("generate_report", "task")],
    "request_approval": [("submit_changes", "task")],
    "finalize_execution": [],
    "deduplicate_error": [("normalize_error", "run")],
    "discover_logs": [],
    "identify_carrier": [("deduplicate_error", "run")],
    "normalize_error": [("discover_logs", "run")],
    "prepare_evidence": [
        ("identify_carrier", "run"),
        ("prepare_source_snapshot", "task"),
        ("prepare_workspace", "task"),
    ],
    "prepare_source_snapshot": [("repair_branch_preflight", "task")],
    "prepare_workspace": [
        ("identify_carrier", "run"),
        ("prepare_source_snapshot", "task"),
    ],
    "repair_branch_preflight": [("identify_carrier", "run")],
}


def dependencies_for(name: str, review_mode: str = "agent") -> tuple[Dependency, ...]:
    """完整执行、单步执行和恢复操作共用的当前步骤依赖。"""
    return tuple(
        Dependency(step, level) for step, level in STEP_DEPENDENCIES.get(name, ())
    )


__all__ = ["STEP_DEPENDENCIES", "dependencies_for"]
