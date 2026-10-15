"""
RepairAttempt - 一次具体的修复尝试

职责：
- 记录修复输入（基线版本、问题证据）
- 记录修复输出（改动文件、修复理由）
- 记录验证结果（是否通过、失败原因）
- 绑定代码版本（确保验证结论与代码一致）

生命周期：从开始修复创建，到验证完成或失败
"""

from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


class AttemptStatus(str, Enum):
    """尝试状态"""
    CREATED = "created"              # 已创建
    REPAIRING = "repairing"          # 正在修复
    REPAIRED = "repaired"            # 修复完成（代码已改）
    VERIFYING = "verifying"          # 正在验证
    VERIFIED = "verified"            # 验证完成
    REVIEWING = "reviewing"          # 正在审查
    REVIEWED = "reviewed"            # 审查完成
    SUCCEEDED = "succeeded"          # 整体成功
    FAILED = "failed"                # 整体失败


class ValidationStatus(str, Enum):
    """验证状态 - 当前代码被证明到什么程度"""
    NOT_RUN = "not_run"              # 未运行
    PASSED = "passed"                # 已通过
    FAILED = "failed"                # 未通过
    INCOMPLETE = "incomplete"        # 部分完成
    STALE = "stale"                  # 已过期（代码已变化）


class BaselineInfo(BaseModel):
    """基线信息"""
    commit: str = Field(description="基线 commit hash")
    branch: str = Field(description="基线分支")
    snapshot_path: Optional[str] = Field(default=None, description="快照路径")


class RepairPlan(BaseModel):
    """修复方案"""
    target_files: List[str] = Field(description="目标文件")
    reasoning: str = Field(description="修复理由")
    approach: str = Field(description="修复方法")
    risks: List[str] = Field(default_factory=list, description="风险点")


class ValidationResult(BaseModel):
    """验证结果"""
    status: ValidationStatus = Field(description="验证状态")
    passed: bool = Field(description="是否通过")
    evidence_id: Optional[str] = Field(default=None, description="证据 ID（指向 artifact）")
    error: Optional[str] = Field(default=None, description="失败原因")
    verified_at: datetime = Field(default_factory=datetime.now, description="验证时间")

    # 版本绑定
    verified_commit: Optional[str] = Field(default=None, description="验证时的 commit hash")
    verified_files_hash: Optional[str] = Field(default=None, description="验证时的文件内容 hash")

    model_config = ConfigDict(use_enum_values=True)


class RepairAttempt(BaseModel):
    """修复尝试"""
    attempt_id: str = Field(description="尝试 ID")
    task_id: str = Field(description="所属任务 ID")
    attempt_number: int = Field(description="尝试序号（1, 2, 3...）")
    status: AttemptStatus = Field(default=AttemptStatus.CREATED, description="尝试状态")

    # 基线
    baseline: Optional[BaselineInfo] = Field(default=None, description="基线信息")

    # 修复方案
    plan: Optional[RepairPlan] = Field(default=None, description="修复方案")

    # 改动
    changed_files: List[str] = Field(default_factory=list, description="改动文件列表")
    diff_artifact_id: Optional[str] = Field(default=None, description="diff 产物 ID")

    # 验证
    response_validation: Optional[ValidationResult] = Field(default=None, description="响应验证结果")
    regression_validation: Optional[ValidationResult] = Field(default=None, description="回归验证结果")
    review_validation: Optional[ValidationResult] = Field(default=None, description="代码审查结果")

    # 时间戳
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    repaired_at: Optional[datetime] = Field(default=None, description="修复完成时间")
    verified_at: Optional[datetime] = Field(default=None, description="验证完成时间")
    finished_at: Optional[datetime] = Field(default=None, description="结束时间")

    # 结果
    success: bool = Field(default=False, description="是否成功")
    failure_reason: Optional[str] = Field(default=None, description="失败原因")

    model_config = ConfigDict(use_enum_values=True)

    def start_repair(self, baseline: BaselineInfo, plan: RepairPlan) -> None:
        """开始修复"""
        self.status = AttemptStatus.REPAIRING
        self.baseline = baseline
        self.plan = plan

    def finish_repair(self, changed_files: List[str], diff_artifact_id: str) -> None:
        """完成修复"""
        self.status = AttemptStatus.REPAIRED
        self.changed_files = changed_files
        self.diff_artifact_id = diff_artifact_id
        self.repaired_at = datetime.now()

    def start_verify(self) -> None:
        """开始验证"""
        self.status = AttemptStatus.VERIFYING

    def finish_verify(
        self,
        response: Optional[ValidationResult] = None,
        regression: Optional[ValidationResult] = None,
    ) -> None:
        """完成验证"""
        if response:
            self.response_validation = response
        if regression:
            self.regression_validation = regression
        self.status = AttemptStatus.VERIFIED
        self.verified_at = datetime.now()

    def start_review(self) -> None:
        """开始审查"""
        self.status = AttemptStatus.REVIEWING

    def finish_review(self, review: ValidationResult) -> None:
        """完成审查"""
        self.review_validation = review
        self.status = AttemptStatus.REVIEWED

    def finish(self, success: bool, reason: Optional[str] = None) -> None:
        """标记结束"""
        self.success = success
        self.status = AttemptStatus.SUCCEEDED if success else AttemptStatus.FAILED
        self.finished_at = datetime.now()
        if reason:
            self.failure_reason = reason

    def is_validated(self) -> bool:
        """是否已验证通过"""
        return (
            self.response_validation
            and self.response_validation.passed
            and self.regression_validation
            and self.regression_validation.passed
        )

    def is_reviewed(self) -> bool:
        """是否已审查通过"""
        return (
            self.review_validation
            and self.review_validation.passed
        )

    def validation_stale(self, current_commit: str, current_files_hash: str) -> bool:
        """验证结果是否已过期"""
        if not self.response_validation or not self.regression_validation:
            return True

        # 检查 commit 或文件内容是否变化
        return (
            self.response_validation.verified_commit != current_commit
            or self.response_validation.verified_files_hash != current_files_hash
            or self.regression_validation.verified_commit != current_commit
            or self.regression_validation.verified_files_hash != current_files_hash
        )
