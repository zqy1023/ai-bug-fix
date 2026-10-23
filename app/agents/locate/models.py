"""根因定位 agent 的输入输出契约。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from app.workflow.spec import FrozenModel


class LocateInput(FrozenModel):
    """定位输入保留 ES 原始问题和确定性预检结果。"""

    # run_id：字段说明见该模型文档与工作流契约。
    run_id: str
    # task_id：字段说明见该模型文档与工作流契约。
    task_id: str
    # attempt：字段说明见该模型文档与工作流契约。
    attempt: int = Field(ge=1)
    retry_context: dict[str, Any] = Field(default_factory=dict)
    # carrier：字段说明见该模型文档与工作流契约。
    carrier: str
    # biztype：字段说明见该模型文档与工作流契约。
    biztype: str
    # service_name：字段说明见该模型文档与工作流契约。
    service_name: str
    # problem：字段说明见该模型文档与工作流契约。
    problem: dict[str, Any]
    # evidence：字段说明见该模型文档与工作流契约。
    evidence: dict[str, Any]
    # workspace_path：只读定位 Agent 唯一允许访问的隔离工作区。
    workspace_path: str
    # source_project_path：本轮共享只读源码树，用于读取框架和公共库证据。
    source_project_path: str = ""
    # source_snapshot：字段说明见该模型文档与工作流契约。
    source_snapshot: dict[str, Any]

    @model_validator(mode="after")
    def validate_problem_identity(self) -> LocateInput:
        if not self.carrier or not self.biztype or not self.service_name:
            raise ValueError("LocateInput 的 carrier、biztype 和 service_name 必须非空")
        for key in ("carrier", "biztype", "service_name"):
            value = self.problem.get(key)
            if not isinstance(value, str) or value != getattr(self, key):
                raise ValueError(f"原始 problem.{key} 与定位任务字段不一致")
        if not self.run_id or not self.task_id or not self.workspace_path:
            raise ValueError("LocateInput 缺少 run_id、task_id 或 workspace_path")
        return self


class SourceEvidence(FrozenModel):
    """一条可复核的项目源码证据。"""

    # path：字段说明见该模型文档与工作流契约。
    path: str
    # symbol：字段说明见该模型文档与工作流契约。
    symbol: str
    # finding：字段说明见该模型文档与工作流契约。
    finding: str
    source_sha256: str = ""
    start_line: int = 0
    end_line: int = 0
    snippet: str = ""


class AcceptanceAssertion(FrozenModel):
    """一条可追溯的验收项，必须声明由哪类业务证据验证。"""

    assertion_id: str = Field(min_length=3, pattern=r"^[a-z][a-z0-9_]{2,63}$")
    expected: str = Field(min_length=8)
    verification_method: Literal["original_response_replay", "request_regression"]


class AcceptanceContract(FrozenModel):
    """根因 Agent 在修改前提出的可执行验收方案；工作流随后冻结其版本。"""

    business_goal: str = Field(min_length=8)
    reproduction: list[str] = Field(min_length=1)
    required_assertions: list[AcceptanceAssertion] = Field(min_length=1)
    regression_cases: list[AcceptanceAssertion] = Field(min_length=1)
    verification_methods: list[str] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    known_limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_actionable_items(self) -> AcceptanceContract:
        for field_name in ("reproduction", "verification_methods"):
            values = getattr(self, field_name)
            if not values or any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f"验收方案 {field_name} 必须包含非空、可执行的条目")
        if not self.business_goal.strip():
            raise ValueError("验收方案 business_goal 不能为空")
        all_items = [*self.required_assertions, *self.regression_cases]
        ids = [item.assertion_id for item in all_items]
        if len(ids) != len(set(ids)):
            raise ValueError("验收方案 assertion_id 必须全局唯一")
        if any(item.verification_method == "original_response_replay" for item in self.regression_cases):
            raise ValueError("回归场景必须使用 request_regression 验证")
        return self


class LocateOutput(FrozenModel):
    """模型协议的严格输出；根因与验收方案一并产出，非 LOCATED 结论不会进入修复阶段。"""

    # status：字段说明见该模型文档与工作流契约。
    status: Literal["LOCATED", "INSUFFICIENT_EVIDENCE", "MANUAL"] = "MANUAL"
    # root_cause：字段说明见该模型文档与工作流契约。
    root_cause: str = ""
    # category：字段说明见该模型文档与工作流契约。
    category: Literal[
        "carrier",
        "shared",
        "downstream",
        "configuration",
        "missing_implementation",
        "unknown",
    ]
    # facts：字段说明见该模型文档与工作流契约。
    facts: list[str] = Field(default_factory=list)
    # source_evidence：字段说明见该模型文档与工作流契约。
    source_evidence: list[SourceEvidence] = Field(default_factory=list)
    # candidate_files：字段说明见该模型文档与工作流契约。
    candidate_files: list[str] = Field(default_factory=list)
    # 调查文件与拟修改目标分离，读取共享证据不代表需要修改共享层。
    proposed_edit_files: list[str] = Field(default_factory=list)
    # evidence_gaps：字段说明见该模型文档与工作流契约。
    evidence_gaps: list[str] = Field(default_factory=list)
    # recommended_scope：字段说明见该模型文档与工作流契约。
    recommended_scope: Literal["carrier", "project", "manual"] = "manual"
    # summary：字段说明见该模型文档与工作流契约。
    summary: str = ""
    # acceptance_contract：根因分析同时给出的修改前验收方案。
    acceptance_contract: AcceptanceContract | None = None
    # passed：字段说明见该模型文档与工作流契约。
    passed: bool = False

    @model_validator(mode="after")
    def validate_located_evidence(self) -> LocateOutput:
        if self.status == "LOCATED":
            missing: list[str] = []
            if not self.root_cause.strip():
                missing.append("根因")
            if not self.source_evidence:
                missing.append("源码证据")
            if not self.candidate_files:
                missing.append("候选文件")
            if self.acceptance_contract is None:
                missing.append("修改前验收方案")
            if missing:
                # 契约不满足时安全降级，而不是抛弃整个模型输出。
                # facts、源码证据、候选文件和根因仍保留给人工排查。
                omitted = {"root_cause", "source_evidence", "candidate_files"} - self.model_fields_set
                reason = ("定位 Agent 输出格式不完整：缺少" if omitted else "定位结果不足以支持自动修改：缺少") + "、".join(missing)
                self.status = "MANUAL"
                self.recommended_scope = "manual"
                self.passed = False
                if reason not in self.evidence_gaps:
                    self.evidence_gaps.append(reason)
                if reason not in self.summary:
                    self.summary = f"{self.summary.strip()}；{reason}".strip("；")
        if self.status != "LOCATED" and self.recommended_scope != "manual":
            self.recommended_scope = "manual"
        if self.status != "LOCATED":
            self.passed = False
        return self


__all__ = ["AcceptanceAssertion", "AcceptanceContract", "LocateInput", "LocateOutput", "SourceEvidence"]
