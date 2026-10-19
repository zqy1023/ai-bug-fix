"""工作流运行时共用的时间工具。

运行状态、任务状态和交付记录由 RecordStore、StepRecord 与 WorkflowState 统一承载；
旧的未接入领域模型不再从这里导出，避免出现第二套状态机。
"""

# 保留原有的 utc_now 函数（向后兼容）
from datetime import datetime, timezone


def utc_now() -> str:
    """返回带时区的 ISO 时间戳，用于步骤记录。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = ["utc_now"]
