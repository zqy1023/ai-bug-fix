"""阶段 4 执行入口节点。"""

from __future__ import annotations

from typing import Any

from agents.review.graph import build_review_pipeline, review


def run(state: dict[str, Any], runtime: Any = None) -> dict[str, Any]:
    """执行审查节点。"""
    return review(state, runtime)


__all__ = ["build_review_pipeline", "run"]
