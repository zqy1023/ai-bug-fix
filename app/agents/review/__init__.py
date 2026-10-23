"""审查 agent。本目录不引用修复实现。

``graph.build`` 编译子图。整图入口是 ``python -m app.entrypoint``。
"""

from __future__ import annotations

from app.agents.review.config import ReviewConfig, ReviewConfigError
from app.agents.review.contract import ReviewVerdict, judge
from app.agents.review.graph import build
from app.agents.review.models import ReviewInput, ReviewView
from app.agents.review.provider import PROVIDERS, create_provider
from app.workflow.spec import AgentSpec, Dependency, RecordView

SPEC = AgentSpec(
    name="review_agent",
    title="审查",
    kind="review",
    input_model=ReviewInput,
    output_model=ReviewView,
    depends_on=(
        Dependency("repair_agent", "task"),
        Dependency("check_changes", "task"),
        Dependency("verify_issue", "task"),
        Dependency("run_regression", "task"),
        Dependency("summarize_validation", "task"),
    ),
    next_step="decide_validation",
    # 审查记录里曾把上游四个节点的输出整份搬进来：repair 的完整 analysis、
    # gate_result 的完整门禁输出（含第二份 diff），以及与 gate_result.diff_text
    # 完全重叠的 diff_summary——input 是 output 的 89 倍。真正属于审查依据的
    # 只有 diff_summary 和三个门禁布尔值，其余各归各步骤的记录。
    input_record=RecordView(
        keep=(
            "task_id",
            "attempt",
            "changed_files",
            "baseline_dir",
            "diff_summary",
            "check_passed",
            "syntax_passed",
            "verify_passed",
            "business_verification_status",
            "regression_passed",
        )
    ),
)

__all__ = [
    "PROVIDERS",
    "SPEC",
    "ReviewConfig",
    "ReviewConfigError",
    "ReviewVerdict",
    "build",
    "create_provider",
    "judge",
]
