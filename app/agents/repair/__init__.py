"""修复 agent。本目录不引用审查实现。

``graph.build`` 编译子图。整图入口是 ``python -m app.entrypoint``。
"""

from __future__ import annotations

from app.agents.repair.config import RepairConfig, RepairConfigError
from app.agents.repair.contract import RepairVerdict, judge
from app.agents.repair.graph import build
from app.agents.repair.models import RepairInput, RepairView
from app.agents.repair.provider import PROVIDERS, create_provider
from app.workflow.spec import AgentSpec, Dependency, RecordView

SPEC = AgentSpec(
    name="repair_agent",
    title="修复",
    kind="repair",
    input_model=RepairInput,
    output_model=RepairView,
    requires_confirmation=True,
    depends_on=(
        Dependency("identify_carrier", "run"),
        Dependency("claim_repair_slot", "task"),
        Dependency("prepare_evidence", "task"),
        Dependency("locate_agent", "task"),
        Dependency("prepare_source_snapshot", "task"),
        Dependency("prepare_workspace", "task"),
    ),
    next_step="check_changes",
    # analysis 是修复核心依据；证据输入只保存候选路径与门禁结论。
    input_record=RecordView(
        trim=(
            ("evidence", ("carrier_dir", "candidates", "legacy_only", "legacy_reason")),
        )
    ),
)

__all__ = [
    "PROVIDERS",
    "SPEC",
    "RepairConfig",
    "RepairConfigError",
    "RepairVerdict",
    "build",
    "create_provider",
    "judge",
]
