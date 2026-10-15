"""
领域模型 - 业务核心

这些模型是业务规则的唯一来源，WorkflowState 只是执行上下文。
"""

# 保留原有的 utc_now 函数（向后兼容）
from datetime import datetime, timezone


def utc_now() -> str:
    """返回带时区的 ISO 时间戳，用于步骤记录。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# 新增的领域模型
from app.domain.models.run import Run, RunConfig, RunSummary, ExecutionStatus
from app.domain.models.repair_task import RepairTask, TaskScope, TaskStatus
from app.domain.models.repair_attempt import (
    RepairAttempt,
    AttemptStatus,
    ValidationStatus,
    BaselineInfo,
    RepairPlan,
    ValidationResult,
)
from app.domain.models.delivery import DeliveryRecord, DeliveryStatus

__all__ = [
    # 向后兼容
    "utc_now",
    # Run
    "Run",
    "RunConfig",
    "RunSummary",
    "ExecutionStatus",
    # RepairTask
    "RepairTask",
    "TaskScope",
    "TaskStatus",
    # RepairAttempt
    "RepairAttempt",
    "AttemptStatus",
    "ValidationStatus",
    "BaselineInfo",
    "RepairPlan",
    "ValidationResult",
    # Delivery
    "DeliveryRecord",
    "DeliveryStatus",
]
