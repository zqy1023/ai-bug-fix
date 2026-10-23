"""只读根因定位 agent。"""

from __future__ import annotations

from app.agents.locate.config import LocateConfig, LocateConfigError
from app.agents.locate.graph import build
from app.agents.locate.models import AcceptanceAssertion, AcceptanceContract, LocateInput, LocateOutput, SourceEvidence
from app.agents.locate.provider import PROVIDERS, create_provider
from app.agents.locate.state import LocateState
from app.workflow.spec import AgentSpec, Dependency, RecordView

SPEC = AgentSpec(
    name="locate_agent",
    title="根因定位",
    kind="locate",
    input_model=LocateInput,
    output_model=LocateOutput,
    depends_on=(
        Dependency("prepare_source_snapshot", "task"),
        Dependency("prepare_evidence", "task"),
        Dependency("prepare_workspace", "task"),
    ),
    next_step="claim_repair_slot",
    input_record=RecordView(
        keep=("task_id", "attempt", "carrier", "biztype", "service_name", "evidence"),
        trim=(("problem", ("carrier", "error", "service_name", "biztype")),),
    ),
)

__all__ = [
    "PROVIDERS",
    "SPEC",
    "LocateConfig",
    "LocateConfigError",
    "AcceptanceAssertion",
    "AcceptanceContract",
    "LocateInput",
    "LocateOutput",
    "LocateState",
    "SourceEvidence",
    "build",
    "create_provider",
]
