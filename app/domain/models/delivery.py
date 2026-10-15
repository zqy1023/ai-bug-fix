"""
DeliveryRecord - 交付记录

职责：
- 记录交付阶段（分支创建、文件应用、提交、推送）
- 记录交付结果（成功、失败、部分成功）
- 支持恢复（重启时根据记录的阶段决定继续或回滚）

生命周期：从开始交付创建，到推送完成或失败
"""

from datetime import datetime
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict


class DeliveryStatus(str, Enum):
    """交付状态 - 代码交付到了什么阶段"""
    NOT_STARTED = "not_started"      # 未开始
    BRANCH_CREATED = "branch_created"  # 分支已创建
    FILES_APPLIED = "files_applied"   # 文件已应用
    COMMITTED = "committed"           # 已提交
    PUSHED = "pushed"                 # 已推送
    FAILED = "failed"                 # 失败


class DeliveryRecord(BaseModel):
    """交付记录"""
    delivery_id: str = Field(description="交付 ID")
    attempt_id: str = Field(description="所属尝试 ID")
    status: DeliveryStatus = Field(default=DeliveryStatus.NOT_STARTED, description="交付状态")

    # Git 信息
    base_branch: str = Field(description="基准分支")
    repair_branch: Optional[str] = Field(default=None, description="修复分支")
    commit_hash: Optional[str] = Field(default=None, description="提交 hash")

    # 交付内容
    changed_files: List[str] = Field(default_factory=list, description="改动文件")
    commit_message: Optional[str] = Field(default=None, description="提交信息")

    # 时间戳
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    branch_created_at: Optional[datetime] = Field(default=None, description="分支创建时间")
    committed_at: Optional[datetime] = Field(default=None, description="提交时间")
    pushed_at: Optional[datetime] = Field(default=None, description="推送时间")
    finished_at: Optional[datetime] = Field(default=None, description="结束时间")

    # 结果
    success: bool = Field(default=False, description="是否成功")
    error: Optional[str] = Field(default=None, description="失败原因")
    failure_stage: Optional[DeliveryStatus] = Field(default=None, description="失败阶段")

    model_config = ConfigDict(use_enum_values=True)

    def branch_created(self, branch_name: str) -> None:
        """分支已创建"""
        self.status = DeliveryStatus.BRANCH_CREATED
        self.repair_branch = branch_name
        self.branch_created_at = datetime.now()

    def files_applied(self, files: List[str]) -> None:
        """文件已应用"""
        self.status = DeliveryStatus.FILES_APPLIED
        self.changed_files = files

    def committed(self, commit_hash: str, message: str) -> None:
        """已提交"""
        self.status = DeliveryStatus.COMMITTED
        self.commit_hash = commit_hash
        self.commit_message = message
        self.committed_at = datetime.now()

    def pushed(self) -> None:
        """已推送"""
        self.status = DeliveryStatus.PUSHED
        self.pushed_at = datetime.now()
        self.success = True
        self.finished_at = datetime.now()

    def fail(self, error: str, stage: DeliveryStatus) -> None:
        """失败"""
        self.status = DeliveryStatus.FAILED
        self.failure_stage = stage
        self.error = error
        self.finished_at = datetime.now()

    def is_terminal(self) -> bool:
        """是否已终止"""
        return self.status in {DeliveryStatus.PUSHED, DeliveryStatus.FAILED}

    def can_resume(self) -> bool:
        """是否可以恢复（从失败点继续）"""
        return (
            self.status != DeliveryStatus.NOT_STARTED
            and not self.is_terminal()
        )
