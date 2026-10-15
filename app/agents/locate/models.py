"""根因定位 agent 的输入输出契约。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from app.workflow.spec import FrozenModel


class LocateInput(FrozenModel):
    """定位输入保留 ES 原始问题和确定性预检结果。"""

    run_id: str
    task_id: str
    attempt: int = Field(ge=1)
    carrier: str
    biztype: str
    service_name: str
    problem: dict[str, Any]
    evidence: dict[str, Any]
    source_project_path: str
    source_snapshot: dict[str, Any]

    @model_validator(mode="after")
    def validate_problem_identity(self) -> LocateInput:
        if not self.carrier or not self.biztype or not self.service_name:
            raise ValueError("LocateInput 的 carrier、biztype 和 service_name 必须非空")
        for key in ("carrier", "biztype", "service_name"):
            value = self.problem.get(key)
            if not isinstance(value, str) or value != getattr(self, key):
                raise ValueError(f"原始 problem.{key} 与定位任务字段不一致")
        if not self.run_id or not self.task_id or not self.source_project_path:
            raise ValueError("LocateInput 缺少 run_id、task_id 或 source_project_path")
        return self


class SourceEvidence(FrozenModel):
    """一条可复核的项目源码证据。"""

    path: str
    symbol: str
    finding: str


class LocateOutput(FrozenModel):
    """模型协议的严格输出；非 LOCATED 结论不会进入修复阶段。"""

    status: Literal["LOCATED", "INSUFFICIENT_EVIDENCE", "MANUAL"] = "MANUAL"
    root_cause: str = ""
    category: Literal[
        "carrier",
        "shared",
        "downstream",
        "configuration",
        "missing_implementation",
        "unknown",
    ]
    facts: list[str] = Field(default_factory=list)
    source_evidence: list[SourceEvidence] = Field(default_factory=list)
    candidate_files: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    recommended_scope: Literal["carrier", "project", "manual"] = "manual"
    summary: str = ""
    passed: bool = False

    @model_validator(mode="after")
    def validate_located_evidence(self) -> LocateOutput:
        if self.status == "LOCATED" and (
            not self.root_cause.strip()
            or not self.source_evidence
            or not self.candidate_files
            or self.recommended_scope == "manual"
        ):
            raise ValueError("LOCATED 必须包含根因、源码证据、候选文件和非 manual 范围")
        if self.status != "LOCATED" and self.recommended_scope != "manual":
            self.recommended_scope = "manual"
        return self


__all__ = ["LocateInput", "LocateOutput", "SourceEvidence"]
