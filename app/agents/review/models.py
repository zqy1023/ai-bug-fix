"""审查 agent 的输入输出契约。

``ReviewInput`` 复用修复 agent 的结论，对应 ``SPEC.depends_on`` 里的
``repair_agent``；审查自己不写文件，回修要求交给下游处理。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.agents.repair.models import RepairView
from app.application.plans import ChangePlan
from app.agents.review.contract import REVIEW_CONTRACT_VERSION
from app.workflow.spec import FrozenModel


class ReviewInput(FrozenModel):
    """09 的输入：只给审查需要的结论，不给修复侧内部状态。"""
    validation_summary: dict[str, Any] = Field(default_factory=dict)

    # task_id：字段说明见该模型文档与工作流契约。
    task_id: str
    # ai_workspace_path：字段说明见该模型文档与工作流契约。
    ai_workspace_path: str = ""
    # attempt：字段说明见该模型文档与工作流契约。
    attempt: int = 1
    retry_context: dict[str, Any] = Field(default_factory=dict)
    # repair：字段说明见该模型文档与工作流契约。
    repair: RepairView = Field(default_factory=RepairView)
    # changed_files：字段说明见该模型文档与工作流契约。
    changed_files: list[str] = Field(default_factory=list)
    # baseline_dir：字段说明见该模型文档与工作流契约。
    baseline_dir: str = ""
    # diff_summary：字段说明见该模型文档与工作流契约。
    diff_summary: str = ""
    # gate_result：字段说明见该模型文档与工作流契约。
    gate_result: dict[str, Any] = Field(default_factory=dict)
    # verify_result：字段说明见该模型文档与工作流契约。
    verify_result: dict[str, Any] = Field(default_factory=dict)
    # regression_result：字段说明见该模型文档与工作流契约。
    regression_result: dict[str, Any] = Field(default_factory=dict)
    # check_passed：字段说明见该模型文档与工作流契约。
    check_passed: bool = False
    syntax_passed: bool = False
    # verify_passed：字段说明见该模型文档与工作流契约。
    verify_passed: bool = False
    business_verification_status: str = "unknown"
    # regression_passed：字段说明见该模型文档与工作流契约。
    regression_passed: bool = False


class CoverageAssessment(FrozenModel):
    """审查针对实际变更判断覆盖是否足够，不能按 syntax_only 自动豁免。"""

    decision: Literal["sufficient", "insufficient", "unknown"] = "unknown"
    reason: str = ""
    reviewed_symbols: list[str] = Field(default_factory=list)
    missing_checks: list[str] = Field(default_factory=list)


class ReviewAgentOutput(FrozenModel):
    """审查步骤对外只留结论、发现项和回修要求。

    ``change_plan`` 是可选的结构化回修方案：审查 agent 恒只读，但它的回修要求
    同样有落盘通道，不再只能塞进 retry_reason 让修复 agent 重新猜。
    """

    # verdict：字段说明见该模型文档与工作流契约。
    verdict: Literal["PASS", "FAIL", "MANUAL"] = "FAIL"
    # passed：字段说明见该模型文档与工作流契约。
    passed: bool = False
    code_verdict: Literal["PASS", "FAIL", "UNKNOWN"] = "UNKNOWN"
    coverage: CoverageAssessment = Field(default_factory=CoverageAssessment)
    # summary：字段说明见该模型文档与工作流契约。
    summary: str = ""
    # findings：字段说明见该模型文档与工作流契约。
    findings: list[str] = Field(default_factory=list)
    # required_changes：字段说明见该模型文档与工作流契约。
    required_changes: list[str] = Field(default_factory=list)
    # change_plan：字段说明见该模型文档与工作流契约。
    change_plan: ChangePlan | None = None


class ReviewView(ReviewAgentOutput):
    """规范化审查状态：批准、可交付和协议执行结果分别保存。"""

    delivery_eligible: bool = False
    review_contract_version: int = REVIEW_CONTRACT_VERSION
    contract_error: str = ""
    evidence_version: dict[str, Any] = Field(default_factory=dict)
    checks: list[dict[str, str]] = Field(default_factory=list)
    next_action: str = ""
    review_notes: str = ""


__all__ = ["ReviewAgentOutput", "ReviewInput", "ReviewView"]
