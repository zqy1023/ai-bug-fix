"""Domain decisions derived from workflow evidence."""

from __future__ import annotations

from typing import Literal

LocateDisposition = Literal["repair", "manual"]


def locate_disposition(status: str) -> LocateDisposition:
    """Only evidence-backed LOCATED results may enter the repair agent."""
    return "repair" if status == "LOCATED" else "manual"


__all__ = ["LocateDisposition", "locate_disposition"]
