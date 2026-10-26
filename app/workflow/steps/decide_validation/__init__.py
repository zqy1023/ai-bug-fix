"""确定性验证节点，输入输出由正式模型限定。"""

from app.workflow.spec import StepSpec
from app.workflow.validation import ValidationDecision, decide
from app.workflow.validation_context import ValidationContext, input_of


def run(ctx, data: ValidationContext) -> ValidationDecision:
    return ValidationDecision.model_validate(decide(data.model_dump(mode="json")))


def patch(output):
    # 步骤没有产出决策（被取消或执行失败）：不伪造终态，交由路由按未完成处理。
    if "action" not in output:
        return {}
    action = str(output.get("action") or "")
    reason = str(output.get("reason") or "")
    manual_required = bool(output.get("manual_required"))
    return {
        "validation_decision": output,
        "retry_target": str(output.get("target") or ""),
        "retry_reason": reason,
        "blocked_reason": reason if action == "blocked" else "",
        "manual_confirmation_required": manual_required,
        "manual_confirmation_reasons": ["用户选择人工代码审查"]
        if manual_required
        else [],
        "terminal_status": "BLOCKED" if action == "blocked" else "passed",
        "last_result": "failed" if action == "blocked" else "passed",
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
