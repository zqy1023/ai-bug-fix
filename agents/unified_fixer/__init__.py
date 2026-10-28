"""统一分析修复 Agent。

单会话端到端闭环完成感知建模、根因定位、规则裁决、受限修补与就地自检反思。
"""

from __future__ import annotations

from agents.unified_fixer.graph import build_graph, run_unified_fixer
from agents.unified_fixer.models import (
    UnifiedFixerConfig,
    UnifiedFixerState,
    SourceEvidence,
    UnifiedFixerInput,
    UnifiedFixerOutput,
    UnifiedFixerView,
    RepairView,
)
from agents.unified_fixer.provider import create_provider
from core.contracts import AgentSpec, RecordView


def build(config: UnifiedFixerConfig | None = None):
    return build_graph(config)


SPEC = AgentSpec(
    name="unified_fixer_agent",
    title="统一分析修复 Agent",
    kind="unified_fixer",
    input_model=UnifiedFixerInput,
    output_model=UnifiedFixerView,
    input_record=RecordView(
        keep=("task_id", "attempt", "carrier", "biztype", "service_name", "evidence"),
        trim=(("problem", ("carrier", "error", "service_name", "biztype")),),
    ),
)

__all__ = [
    "SPEC",
    "SourceEvidence",
    "UnifiedFixerConfig",
    "UnifiedFixerInput",
    "UnifiedFixerOutput",
    "UnifiedFixerState",
    "UnifiedFixerView",
    "RepairView",
    "build",
    "create_provider",
    "run_unified_fixer",
]
