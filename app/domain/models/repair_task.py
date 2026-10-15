"""
RepairTask - 针对一个运输商、一个问题的修复任务

职责：
- 标识唯一问题（carrier + normalized_error）
- 跟踪修复状态（等待、进行中、成功、失败）
- 关联多次修复尝试（可能需要重试）

生命周期：从问题识别创建，到修复成功或放弃
"""

from datetime import datetime
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict


class TaskStatus(str, Enum):
    """任务状态"""
    PENDING = "pending"              # 已创建，等待执行
    CLAIMED = "claimed"              # 已领取，准备修复
    REPAIRING = "repairing"          # 正在修复
    VERIFYING = "verifying"          # 正在验证
    REVIEWING = "reviewing"          # 正在审查
    WAITING_APPROVAL = "waiting_approval"  # 等待人工审批
    DELIVERING = "delivering"        # 正在交付
    SUCCEEDED = "succeeded"          # 修复成功
    FAILED = "failed"                # 修复失败
    MANUAL = "manual"                # 转人工处理


class TaskScope(BaseModel):
    """任务范围"""
    carrier: str = Field(description="运输商")
    normalized_error: str = Field(description="归一化错误信息")
    error_signature: str = Field(description="错误签名（用于去重）")


class RepairTask(BaseModel):
    """修复任务"""
    task_id: str = Field(description="任务 ID")
    run_id: str = Field(description="所属运行 ID")
    scope: TaskScope = Field(description="任务范围")
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="任务状态")

    # 时间戳
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    claimed_at: Optional[datetime] = Field(default=None, description="领取时间")
    finished_at: Optional[datetime] = Field(default=None, description="结束时间")

    # 修复尝试
    attempt_count: int = Field(default=0, description="尝试次数")
    max_attempts: int = Field(default=3, description="最大尝试次数")
    current_attempt_id: Optional[str] = Field(default=None, description="当前尝试 ID")

    # 结果
    success: bool = Field(default=False, description="是否成功")
    error: Optional[str] = Field(default=None, description="失败原因")
    manual_reason: Optional[str] = Field(default=None, description="转人工原因")

    # Git 信息
    repair_branch: Optional[str] = Field(default=None, description="修复分支名")

    model_config = ConfigDict(use_enum_values=True)

    def claim(self, attempt_id: str) -> None:
        """领取任务"""
        self.status = TaskStatus.CLAIMED
        self.claimed_at = datetime.now()
        self.current_attempt_id = attempt_id
        self.attempt_count += 1

    def start_repair(self) -> None:
        """开始修复"""
        self.status = TaskStatus.REPAIRING

    def start_verify(self) -> None:
        """开始验证"""
        self.status = TaskStatus.VERIFYING

    def start_review(self) -> None:
        """开始审查"""
        self.status = TaskStatus.REVIEWING

    def wait_approval(self) -> None:
        """等待审批"""
        self.status = TaskStatus.WAITING_APPROVAL

    def start_delivery(self) -> None:
        """开始交付"""
        self.status = TaskStatus.DELIVERING

    def finish(self, success: bool, error: Optional[str] = None) -> None:
        """标记结束"""
        self.success = success
        self.status = TaskStatus.SUCCEEDED if success else TaskStatus.FAILED
        self.finished_at = datetime.now()
        if error:
            self.error = error

    def manual(self, reason: str) -> None:
        """转人工"""
        self.status = TaskStatus.MANUAL
        self.manual_reason = reason
        self.finished_at = datetime.now()

    def is_terminal(self) -> bool:
        """是否已终止"""
        return self.status in {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.MANUAL,
        }

    def can_retry(self) -> bool:
        """是否可以重试"""
        return (
            not self.is_terminal()
            and self.attempt_count < self.max_attempts
        )
