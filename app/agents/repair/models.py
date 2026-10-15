"""修复 agent 的输入输出契约。

``RepairInput`` 复用 ``prepare_evidence`` 的结构化结果；提示词和工具轨迹不进步骤记录。
"""

from __future__ import annotations

from typing import Any, Literal

from app.workflow.spec import FrozenModel
from app.workflow.steps.prepare_evidence.models import EvidenceOutput
from pydantic import Field


class RepairInput(FrozenModel):
    """05 的输入：定位 + 上一轮驳回原因，不含提示词全文。"""

    task_id: str
    ai_workspace_path: str
    attempt: int = 1
    retry_reason: str = ""
    evidence: EvidenceOutput = Field(default_factory=EvidenceOutput)
    locate: dict[str, Any] = Field(default_factory=dict)


class RepairView(FrozenModel):
    """修复步骤对外只保留核心结论，去脂提效。

    ``changed_files`` 必须留在视图里：它是模型**自己声明**的修改清单，下游
    ``check_changes`` 会拿它和工作区实际 diff 对账（不一致即判失败）。早先这个
    字段只存在于子图返回的字典里，没进视图，落盘后就没了——审计链断在第一步，
    报告里也查不出模型声称改了什么。

    ``change_plan`` 是结构化方案：根因落在共享层时它仍然完整保留，等待人工授权后
    由 ``apply_plan`` 落盘，不再因为 ``passed=false`` 就无声消失。**本轮改动直接
    落在目标 carrier 内时它为空是正常的**，它只在需要人工授权时才非空。
    """

    passed: bool = False
    result: Literal["passed", "failed"] = "failed"
    analysis: str = ""
    summary: str = ""
    changed_files: list[str] = Field(default_factory=list)
    change_plan: dict[str, Any] = Field(default_factory=dict)
    blocked_reason: str = ""
    scope_escalation_required: bool = False


__all__ = ["RepairInput", "RepairView"]
