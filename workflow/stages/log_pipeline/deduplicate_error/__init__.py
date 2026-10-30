"""按已规范化的真实记录和配置过滤、去重。"""

from __future__ import annotations

from typing import Any

from core.contracts import StepSpec
from .models import (
    DeduplicateErrorInput,
    DeduplicateErrorOutput,
)
from .node import run


def _input(state: dict[str, Any]) -> dict[str, Any]:
    return {"run_id": str(state.get("run_id") or "")}


SPEC = StepSpec(
    name="deduplicate_error",
    title="过滤并去重异常",
    runner=run,
    input_model=DeduplicateErrorInput,
    output_model=DeduplicateErrorOutput,
    input_builder=_input,
    task_level=False,
)

__all__ = ["SPEC"]

