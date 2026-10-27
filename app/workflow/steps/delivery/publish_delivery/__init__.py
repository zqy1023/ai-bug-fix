"""交付修复分支：AI 审查通过后直接交付，人工复核只决定保留还是打回。"""

from typing import Any

from app.domain.errors import StepFailed
from app.workflow.spec import StepSpec
from app.workflow.steps.delivery.prepare_delivery import _input
from app.workflow.steps.delivery.prepare_delivery.models import DeliveryInput, DeliveryOutput
from app.workflow.steps.delivery.prepare_delivery.node import run


def input_of(state):
    # 正式交付必须与准备阶段生成的候选是同一个提交：candidate_id 绑定验证证据、
    # 分支与文件清单，避免在两次步骤之间换掉补丁或分支。
    candidate = state.get("delivery_candidate")
    candidate_id = (
        str(candidate.get("candidate_id") or "") if isinstance(candidate, dict) else ""
    )
    if not candidate_id:
        raise StepFailed(
            "正式交付缺少准备阶段的候选身份", code="DELIVERY_CANDIDATE_MISSING"
        )
    return {
        **_input(state),
        "publish": True,
        "delivery_candidate_id": candidate_id,
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    # 步骤没有产出交付结果（被取消或执行失败）：不回写任何状态。
    if "pushed" not in output:
        return {}
    return {
        "delivery": output,
        "delivery_pushed": bool(output.get("pushed")),
        "delivery_local_branch": bool(output.get("local_branch")),
        "delivery_commit": str(output.get("commit") or ""),
    }


SPEC = StepSpec(
    name="publish_delivery",
    title="正式交付修复分支",
    runner=run,
    input_model=DeliveryInput,
    output_model=DeliveryOutput,
    input_builder=input_of,
    output_patch=_patch,
)

