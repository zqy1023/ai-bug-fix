"""确定性验证节点，输入输出由正式模型限定。"""

from app.workflow.spec import StepSpec
from app.workflow.validation import ValidationSummary, summarize
from app.workflow.validation_context import ValidationContext, input_of


def run(ctx, data: ValidationContext) -> ValidationSummary:
    return ValidationSummary.model_validate(summarize(data.model_dump(mode="json")))


def patch(output):
    return {"validation_summary": output, "last_result": "passed"}


SPEC = StepSpec(
    name="summarize_validation",
    title="汇总断言结果",
    runner=run,
    input_model=ValidationContext,
    output_model=ValidationSummary,
    input_builder=input_of,
    output_patch=patch,
)
