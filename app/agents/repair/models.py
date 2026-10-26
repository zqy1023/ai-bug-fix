"""修复 agent 的输入输出契约。

``RepairInput`` 复用 ``prepare_evidence`` 的结构化结果；提示词和工具轨迹不进步骤记录。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.application.plans import ChangePlan
from app.workflow.contracts import AcceptanceContract, BaselineReproduction
from app.workflow.spec import FrozenModel
from app.workflow.steps.prepare_evidence.models import EvidenceOutput


class RepairInput(FrozenModel):
    acceptance_contract: AcceptanceContract
    baseline_reproduction: BaselineReproduction
    """05 的输入：定位 + 上一轮驳回原因，不含提示词全文。"""

    # task_id：字段说明见该模型文档与工作流契约。
    task_id: str
    # ai_workspace_path：字段说明见该模型文档与工作流契约。
    ai_workspace_path: str
    # attempt：字段说明见该模型文档与工作流契约。
    attempt: int = 1
    # retry_reason：字段说明见该模型文档与工作流契约。
    retry_reason: str = ""
    retry_context: dict[str, Any] = Field(default_factory=dict)
    # evidence：字段说明见该模型文档与工作流契约。
    evidence: EvidenceOutput = Field(default_factory=EvidenceOutput)
    # locate：字段说明见该模型文档与工作流契约。
    locate: dict[str, Any] = Field(default_factory=dict)


class RepairAgentOutput(FrozenModel):
    """修复步骤对外只保留核心结论，去脂提效。

    ``changed_files`` 必须留在视图里：它是模型**自己声明**的修改清单，下游
    ``check_changes`` 会拿它和工作区实际 diff 对账（不一致即判失败）。早先这个
    字段只存在于子图返回的字典里，没进视图，落盘后就没了——审计链断在第一步，
    报告里也查不出模型声称改了什么。

    ``change_plan`` 是结构化分析：根因落在共享层时仍然保留以供终态报告记录。
    运输商内的修改同样保留理由、风险和验证条件，下游逐项核对执行证据。
    """

    # passed：字段说明见该模型文档与工作流契约。
    passed: bool = False
    # result：字段说明见该模型文档与工作流契约。
    result: Literal["passed", "failed"] = "failed"
    # analysis：字段说明见该模型文档与工作流契约。
    analysis: str = ""
    # summary：字段说明见该模型文档与工作流契约。
    summary: str = ""
    # changed_files：字段说明见该模型文档与工作流契约。
    changed_files: list[str] = Field(default_factory=list)
    # change_plan：字段说明见该模型文档与工作流契约。
    change_plan: ChangePlan | None = None
    # blocked_reason：字段说明见该模型文档与工作流契约。
    blocked_reason: str = ""
    # scope_escalation_required：字段说明见该模型文档与工作流契约。
    scope_escalation_required: bool = False


class RepairView(RepairAgentOutput):
    """平台派生的失败类别不要求模型生成。"""

    failure_kind: str = ""


__all__ = ["RepairAgentOutput", "RepairInput", "RepairView"]
