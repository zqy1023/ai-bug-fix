"""确定后续动作：平台确定性节点，不调用模型。"""
from typing import Any
from app.workflow.spec import StepSpec, FrozenModel, Dependency
from app.workflow.validation import decision_patch

class Input(FrozenModel):
    state: dict[str, Any]

class Output(FrozenModel):
    payload: dict[str, Any]

def run(ctx, data: Input) -> Output:
    return Output(payload=decision_patch(data.state))

SPEC = StepSpec(next_step="finalize_execution", name="decide_validation", title="确定后续动作", runner=run, input_model=Input,
                output_model=Output, input_builder=lambda state: {"state": {key: state[key] for key in ['check_passed', 'syntax_passed', 'gate_result', 'ai_workspace_path', 'baseline_dir', 'changed_files', 'problem', 'validation_plan', 'verify_result', 'regression_result', 'validation_summary', 'review', 'validation_recovery_counts', 'automatic_attempt', 'max_attempts', 'review_mode'] if key in state}},
                output_patch=lambda output: dict(output["payload"]), depends_on=(Dependency("review_agent", "task"),))
