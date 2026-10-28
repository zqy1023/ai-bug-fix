"""审查 agent。本目录不引用修复实现。

``graph.build`` 编译子图。
"""

from __future__ import annotations

from agents.review.contract import (
    ReviewConfig,
    ReviewConfigError,
    ReviewInput,
    ReviewVerdict,
    ReviewView,
    judge,
)
from agents.review.graph import PROVIDERS, build, create_provider
from core.contracts import AgentSpec, RecordView

SPEC = AgentSpec(
    name="review_agent",
    title="修复后代码审查 Agent",
    kind="review",
    input_model=ReviewInput,
    output_model=ReviewView,
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
