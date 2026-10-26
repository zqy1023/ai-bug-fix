"""审批通过后交付同一候选提交，不重新生成补丁。"""

from typing import Any

from app.domain.errors import StepFailed
from app.workflow.spec import StepSpec
from app.workflow.steps.prepare_delivery import _input
from app.workflow.steps.prepare_delivery.models import DeliveryInput, DeliveryOutput
from app.workflow.steps.prepare_delivery.node import run


def input_of(state):
    approval = state.get("approval_result") or {}
    if str(approval.get("decision") or "").upper() != "APPROVE":
        # 裸 ValueError 会被折叠成通用执行失败，运维看不出是"没审批就交付"。
        raise StepFailed(
            "正式交付需要人工批准", code="APPROVAL_CONTEXT_MISSING"
        )
    return {
        **_input(state),
        "publish": True,
        "approved_candidate_id": str(approval.get("candidate_id") or ""),
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    # 步骤没有产出交付结果（被取消或执行失败）：不回写任何状态。
    if "pushed" not in output:
        return {}
    pushed = bool(output.get("pushed"))
    patch: dict[str, Any] = {
        "delivery": output,
        "delivery_pushed": pushed,
        "delivery_commit": str(output.get("commit") or ""),
    }
    # 终态只由真正推送成功的交付写入；未推送时由 finalize_execution 收尾。
    if pushed:
        patch["terminal_status"] = "DELIVERED"
    return patch


SPEC = StepSpec(
    name="publish_delivery",
    title="正式交付修复分支",
    runner=run,
    input_model=DeliveryInput,
    output_model=DeliveryOutput,
    input_builder=input_of,
    output_patch=_patch,
)
