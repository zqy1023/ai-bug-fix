"""验证节点接收的显式字段，拒绝任意图状态混入输入。"""

from typing import Any

from pydantic import Field

from app.workflow.spec import FrozenModel


class ValidationContext(FrozenModel):
    ai_workspace_path: str
    baseline_dir: str
    baseline_manifest: dict[str, str]
    problem: dict[str, Any]
    acceptance_contract: dict[str, Any]
    baseline_reproduction: dict[str, Any]
    changed_files: list[str] = Field(default_factory=list)
    syntax_result: dict[str, Any] = Field(default_factory=dict)
    gate_result: dict[str, Any] = Field(default_factory=dict)
    validation_plan: dict[str, Any] = Field(default_factory=dict)
    validation_summary: dict[str, Any] = Field(default_factory=dict)
    verify_result: dict[str, Any] = Field(default_factory=dict)
    regression_result: dict[str, Any] = Field(default_factory=dict)
    review: dict[str, Any] = Field(default_factory=dict)
    validation_recovery_counts: dict[str, int] = Field(default_factory=dict)
    automatic_attempt: int = 1
    max_attempts: int = 3
    review_mode: str = "agent"


def input_of(state: dict[str, Any]) -> dict[str, Any]:
    return {key: state[key] for key in ValidationContext.model_fields if key in state}
