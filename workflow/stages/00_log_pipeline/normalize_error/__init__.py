"""标准化真实日志错误文本。"""

from __future__ import annotations

from typing import Any

from core.contracts import StepSpec
from .models import (
    NormalizeErrorInput,
    NormalizeErrorOutput,
)
from .node import run


def _input(state: dict[str, Any]) -> dict[str, Any]:
    return {"run_id": str(state.get("run_id") or "")}


SPEC = StepSpec(
    name="normalize_error",
    title="规范异常日志",
    runner=run,
    input_model=NormalizeErrorInput,
    output_model=NormalizeErrorOutput,
    input_builder=_input,
    task_level=False,
)

__all__ = ["SPEC"]

