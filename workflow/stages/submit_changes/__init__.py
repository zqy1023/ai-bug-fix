"""提交候选分支：将修复分支推送到远程版本库。"""

from typing import Any

from core.errors import StepFailed
from core.contracts import StepSpec
from workflow.stages.generate_report import _input
from workflow.stages.generate_report.models import DeliveryInput, DeliveryOutput
from workflow.stages.generate_report.node import run


def input_of(state):
    candidate = state.get("delivery_candidate")
    candidate_id = (
        str(candidate.get("candidate_id") or "") if isinstance(candidate, dict) else ""
    )
    if not candidate_id:
        raise StepFailed(
            "提交分支缺少准备阶段的候选身份", code="DELIVERY_CANDIDATE_MISSING"
        )
    return {
        **_input(state),
        "publish": True,
        "delivery_candidate_id": candidate_id,
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    if "pushed" not in output:
        return {}
    return {
        "delivery": output,
        "delivery_pushed": bool(output.get("pushed")),
        "delivery_local_branch": bool(output.get("local_branch")),
        "delivery_commit": str(output.get("commit") or ""),
    }


SPEC = StepSpec(
    name="submit_changes",
    title="提交候选分支",
    runner=run,
    input_model=DeliveryInput,
    output_model=DeliveryOutput,
    input_builder=input_of,
    output_patch=_patch,
)

__all__ = ["SPEC", "input_of", "_patch"]
