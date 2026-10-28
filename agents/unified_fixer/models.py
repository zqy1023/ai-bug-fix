"""统一分析修复 Agent 的输入输出契约与配置/状态模型。

单会话端到端完成：异常感知与动态验收建模、假设驱动定位、规则与准入裁决、受限修补、AST 自检与反思自愈。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, TypedDict
from pydantic import BaseModel, Field, model_validator

from core.contracts import ChangePlan, FrozenModel


class SourceEvidence(FrozenModel):
    """一条可复核的项目源码证据。"""

    path: str
    symbol: str
    finding: str
    source_sha256: str = ""
    start_line: int = 0
    end_line: int = 0
    snippet: str = ""


class UnifiedFixerInput(FrozenModel):
    """统一分析修复 Agent 的输入。包含完整原始问题、预检证据与隔离工作区。"""

    run_id: str
    task_id: str
    carrier: str
    biztype: str
    service_name: str
    problem: dict[str, Any]
    evidence: dict[str, Any]
    workspace_path: str
    source_project_path: str = ""
    source_snapshot: dict[str, Any] = Field(default_factory=dict)
    attempt: int = Field(default=1, ge=1)
    retry_reason: str = ""
    retry_context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_identity(self) -> UnifiedFixerInput:
        if not self.carrier or not self.biztype or not self.service_name:
            raise ValueError("UnifiedFixerInput 的 carrier、biztype 和 service_name 必须非空")
        if not self.run_id or not self.task_id or not self.workspace_path:
            raise ValueError("UnifiedFixerInput 缺少 run_id、task_id 或 workspace_path")
        return self


class UnifiedFixerOutput(FrozenModel):
    """统一分析修复 Agent 的最终落盘契约。

    既包含定位分析的归因与证据链，也包含实际代码补丁与即时自检结果。
    """

    status: Literal["FIXED", "ANALYSIS_ONLY", "INSUFFICIENT_EVIDENCE", "MANUAL"] = "MANUAL"
    root_cause: str = ""
    category: Literal[
        "carrier",
        "shared",
        "downstream",
        "configuration",
        "missing_implementation",
        "unknown",
    ] = "unknown"
    attribution_type: Literal[
        "code",
        "official",
        "environment",
        "mixed",
        "pending",
    ] = "pending"
    facts: list[str] = Field(default_factory=list)
    source_evidence: list[SourceEvidence] = Field(default_factory=list)
    candidate_files: list[str] = Field(default_factory=list)
    proposed_edit_files: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    change_plan: ChangePlan | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)
    self_check: dict[str, Any] = Field(default_factory=dict)
    blocked_reason: str = ""
    scope_escalation_required: bool = False
    evidence_gaps: list[str] = Field(default_factory=list)
    recommended_scope: Literal["carrier", "project", "manual"] = "manual"
    summary: str = ""
    analysis: str = ""
    passed: bool = False
    result: Literal["passed", "failed"] = "failed"


class UnifiedFixerView(UnifiedFixerOutput):
    """平台派生与可展示视图。"""

    failure_kind: str = ""


RepairView = UnifiedFixerView


class UnifiedFixerConfig(BaseModel):
    """统一 Agent 的单会话与行为参数配置。"""

    timeout_seconds: int = Field(default=600, ge=30, le=1800)
    max_turns: int = Field(default=15, ge=1, le=50)
    max_self_healing_turns: int = Field(default=2, ge=0, le=5)
    max_context_chars: int = Field(default=120000, ge=1000)
    max_candidate_files: int = Field(default=5, ge=1, le=20)
    model: str = ""

    @classmethod
    def load(cls) -> UnifiedFixerConfig:
        config_file = Path(__file__).resolve().parent / "config.json"
        if config_file.is_file():
            try:
                data = json.loads(config_file.read_text(encoding="utf-8"))
                return cls.model_validate(data)
            except Exception:
                pass
        return cls()


class UnifiedFixerState(TypedDict, total=False):
    """统一分析修复 Agent 内部单会话执行状态。"""

    # 任务标识与环境
    run_id: str
    task_id: str
    attempt: int
    carrier: str
    biztype: str
    service_name: str
    workspace_path: str
    source_project_path: str
    problem: dict[str, Any]
    evidence: dict[str, Any]
    source_snapshot: dict[str, Any]
    retry_reason: str
    retry_context: dict[str, Any]
    agent_policy: dict[str, Any]

    # 单会话中间产物
    acceptance_criteria: list[str]
    locate_result: dict[str, Any]
    decision: str  # EARLY_EXIT / PROCEED_PATCH
    change_plan: dict[str, Any]
    patch_result: dict[str, Any]
    self_check_passed: bool
    self_healing_attempts: int

    # 最终业务产物
    status: str
    root_cause: str
    category: str
    attribution_type: str
    facts: list[str]
    source_evidence: list[dict[str, Any]]
    candidate_files: list[str]
    proposed_edit_files: list[str]
    changed_files: list[str]
    blocked_reason: str
    scope_escalation_required: bool
    evidence_gaps: list[str]
    recommended_scope: str
    summary: str
    analysis: str
    passed: bool
    result: str

    unified_fixer: dict[str, Any]
    locate: dict[str, Any]
    repair: dict[str, Any]
    analysis_only: bool
    last_result: str
    self_check: dict[str, Any]
    change_plan: dict[str, Any] | None
    acceptance_criteria: list[str]
    usage: dict[str, Any]
    metadata: dict[str, Any]

    # 元数据与审计
    token_usage: dict[str, Any]
    tool_calls: list[dict[str, Any]]
    turn_count: int


__all__ = [
    "SourceEvidence",
    "UnifiedFixerInput",
    "UnifiedFixerOutput",
    "UnifiedFixerView",
    "RepairView",
    "UnifiedFixerConfig",
    "UnifiedFixerState",
]
