"""修改后绑定验证证据：验收目标来自修改前冻结的根因 Agent 契约。"""
from typing import Any
from app.workflow.spec import StepSpec, FrozenModel, Dependency
from app.workflow.validation import make_plan

class Input(FrozenModel):
    state: dict[str, Any]

class Output(FrozenModel):
    payload: dict[str, Any]

def run(ctx, data: Input) -> Output:
    return Output(payload=make_plan(data.state))

SPEC = StepSpec(next_step="verify_issue", name="plan_validation", title="绑定验收方案与变更证据", runner=run, input_model=Input,
                output_model=Output, input_builder=lambda state: {"state": {key: state[key] for key in ['check_passed', 'syntax_passed', 'gate_result', 'ai_workspace_path', 'baseline_dir', 'baseline_manifest', 'changed_files', 'problem', 'review_mode', 'acceptance_contract', 'acceptance_contract_id', 'acceptance_contract_meta'] if key in state}},
                output_patch=lambda output: {"validation_plan": output["payload"], "last_result": "passed"}, depends_on=(Dependency("syntax_regression", "task"),))
