"""审批通过后交付同一候选提交，不重新生成补丁。"""

from app.workflow.spec import StepSpec
from app.workflow.steps.prepare_delivery import _input
from app.workflow.steps.prepare_delivery.models import DeliveryInput, DeliveryOutput
from app.workflow.steps.prepare_delivery.node import run


def input_of(state):
    approval = state.get("approval_result") or {}
    if approval.get("decision") != "APPROVE":
        raise ValueError("APPROVAL_REQUIRED: 正式交付需要人工批准")
    return {
        **_input(state),
        "publish": True,
        "approved_candidate_id": approval["candidate_id"],
    }


SPEC = StepSpec(
    name="publish_delivery",
    title="正式交付修复分支",
    runner=run,
    input_model=DeliveryInput,
    output_model=DeliveryOutput,
    input_builder=input_of,
    output_patch=lambda output: {
        "delivery": output,
        "delivery_pushed": output["pushed"],
        "delivery_commit": output["commit"],
        "terminal_status": "DELIVERED",
    },
)
