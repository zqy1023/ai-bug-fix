"""确定性验证节点，输入输出由正式模型限定。"""

from core.contracts import StepSpec
from workflow.engine.validation import ValidationPlan, make_plan
from workflow.engine.validation_context import ValidationContext, input_of


def run(ctx, data: ValidationContext) -> ValidationPlan:
    return ValidationPlan.model_validate(make_plan(data.model_dump(mode="json")))


def patch(output):
    return {"validation_plan": output, "last_result": "passed"}


SPEC = StepSpec(
    name="plan_validation",
    title="建立补丁验证计划",
    runner=run,
    input_model=ValidationContext,
    output_model=ValidationPlan,
    input_builder=input_of,
    output_patch=patch,
)
