"""Shared approval decisions and retry-node contract.

The API, runner, router and approval node must agree on the exact retry targets.
Keep the whitelist here so an omitted/invalid target can never silently become repair_agent.
"""

from __future__ import annotations

from typing import Literal, get_args

RetryTarget = Literal[
    "locate_agent",
    "repair_agent",
    "validate_changes",
    "run_regression",
    "review_agent",
    "prepare_delivery",
]

APPROVAL_RETRY_TARGETS = frozenset(get_args(RetryTarget))

__all__ = ["APPROVAL_RETRY_TARGETS", "RetryTarget"]
