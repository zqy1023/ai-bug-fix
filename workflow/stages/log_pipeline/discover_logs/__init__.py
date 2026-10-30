"""拉取、解析并标准化异常日志。"""

from __future__ import annotations

from typing import Any

from infrastructure.llm.config import log_hours_from_values
from core.contracts import StepSpec
from .models import DiscoverInput, DiscoverOutput
from .node import run
from config.settings import settings


def _input(state: dict[str, Any]) -> dict[str, Any]:
    """记录并传递本轮实际查询条件。"""
    return {
        "service_name": str(state["log_service_name"]),
        "service_prefix": str(state["log_service_prefix"]),
        "excluded_messages": list(state.get("log_excluded_messages") or []),
        "excluded_biztypes": list(state.get("log_excluded_biztypes") or []),
        "hours": log_hours_from_values(state, default=settings.default_log_hours),
        "limit": int(state["log_limit"]),
    }


SPEC = StepSpec(
    name="discover_logs",
    title="准备日志查询",
    runner=run,
    input_model=DiscoverInput,
    output_model=DiscoverOutput,
    input_builder=_input,
    task_level=False,
)

__all__ = ["SPEC"]

