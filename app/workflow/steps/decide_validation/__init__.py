"""确定性验证节点，输入输出由正式模型限定。"""

from app.workflow.spec import StepSpec
from app.workflow.validation import ValidationDecision, decide
from app.workflow.validation_context import ValidationContext, input_of


def run(ctx, data: ValidationContext) -> ValidationDecision:
    return ValidationDecision.model_validate(decide(data.model_dump(mode="json")))


def patch(output):
    return {
        "validation_decision": output,
        "retry_target": output["target"],
        "retry_reason": output["reason"],
        "blocked_reason": output["reason"] if output["action"] == "blocked" else "",
        "manual_confirmation_required": output["manual_required"],
        "manual_confirmation_reasons": ["用户选择人工代码审查"]
        if output["manual_required"]
        else [],
        "terminal_status": "BLOCKED" if output["action"] == "blocked" else "passed",
        "last_result": "passed",
    }


SPEC = StepSpec(
    name="decide_validation",
    title="确定后续动作",
    runner=run,
    input_model=ValidationContext,
    output_model=ValidationDecision,
    input_builder=input_of,
    output_patch=patch,
)
