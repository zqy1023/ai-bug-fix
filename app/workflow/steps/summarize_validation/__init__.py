"""汇总验证事实：平台确定性节点，不调用模型。"""
from typing import Any
from app.workflow.spec import StepSpec, FrozenModel, Dependency
from app.workflow.validation import summarize

class Input(FrozenModel):
    state: dict[str, Any]

class Output(FrozenModel):
    payload: dict[str, Any]

def run(ctx, data: Input) -> Output:
    return Output(payload=summarize(data.state))

SPEC = StepSpec(next_step="review_agent", name="summarize_validation", title="汇总验证事实", runner=run, input_model=Input,
                output_model=Output, input_builder=lambda state: {"state": {key: state[key] for key in ['check_passed', 'syntax_passed', 'gate_result', 'ai_workspace_path', 'baseline_dir', 'baseline_manifest', 'changed_files', 'problem', 'validation_plan', 'acceptance_contract', 'acceptance_contract_id', 'acceptance_contract_meta', 'verify_result', 'regression_result'] if key in state}},
                output_patch=lambda output: {"validation_summary": output["payload"], "last_result": "passed"}, depends_on=(Dependency("run_regression", "task"),))
