"""时间戳。步骤状态模型在 ``records``。"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> str:
    """返回带时区的 ISO 时间戳，用于步骤记录。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = ["utc_now"]