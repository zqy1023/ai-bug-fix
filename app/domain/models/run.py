"""
Run - 一次完整的工作流执行

职责：
- 记录执行配置（输入日志范围、运行模式、Agent 配置）
- 追踪执行状态（运行中、等待审批、成功、失败）
- 汇总任务结果（处理了多少问题、多少成功、多少失败）

生命周期：从 start_run 创建，到所有任务完成或取消
"""

from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


class ExecutionStatus(str, Enum):
    """执行状态 - 任务当前正在做什么"""
    PENDING = "pending"              # 已创建，等待开始
    RUNNING = "running"              # 正在执行
    WAITING_APPROVAL = "waiting_approval"  # 等待人工审批
    SUCCEEDED = "succeeded"          # 所有任务成功
    FAILED = "failed"                # 至少一个任务失败
    CANCELLED = "cancelled"          # 用户取消


class RunConfig(BaseModel):
    """运行配置"""
    log_source: str = Field(description="日志来源（ES、文件等）")
    log_query: Dict[str, Any] = Field(description="日志查询条件")
    carriers: Optional[List[str]] = Field(default=None, description="限定运输商范围")
    dry_run: bool = Field(default=False, description="是否为试运行")
    max_tasks: int = Field(default=10, description="最大并发任务数")
    agent_config: Dict[str, Any] = Field(default_factory=dict, description="Agent 配置")


class RunSummary(BaseModel):
    """运行汇总"""
    total_logs: int = Field(default=0, description="总日志数")
    normalized_logs: int = Field(default=0, description="归一化成功数")
    identified_problems: int = Field(default=0, description="识别出的问题数")
    tasks_created: int = Field(default=0, description="创建的任务数")
    tasks_succeeded: int = Field(default=0, description="成功任务数")
    tasks_failed: int = Field(default=0, description="失败任务数")
    tasks_manual: int = Field(default=0, description="转人工任务数")


class Run(BaseModel):
    """工作流执行记录"""
    run_id: str = Field(description="运行 ID")
    config: RunConfig = Field(description="运行配置")
    status: ExecutionStatus = Field(default=ExecutionStatus.PENDING, description="执行状态")
    summary: RunSummary = Field(default_factory=RunSummary, description="运行汇总")

    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    started_at: Optional[datetime] = Field(default=None, description="开始时间")
    finished_at: Optional[datetime] = Field(default=None, description="结束时间")

    error: Optional[str] = Field(default=None, description="失败原因")
    checkpoint_id: Optional[str] = Field(default=None, description="LangGraph checkpoint ID")

    model_config = ConfigDict(use_enum_values=True)

    def start(self) -> None:
        """标记开始"""
        self.status = ExecutionStatus.RUNNING
        self.started_at = datetime.now()

    def finish(self, status: ExecutionStatus, error: Optional[str] = None) -> None:
        """标记结束"""
        self.status = status
        self.finished_at = datetime.now()
        if error:
            self.error = error

    def is_terminal(self) -> bool:
        """是否已终止"""
        return self.status in {
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
        }
