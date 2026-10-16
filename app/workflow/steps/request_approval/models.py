"""人工审批的输入输出契约。"""

from __future__ import annotations

from typing import Literal

from app.workflow.spec import FrozenModel
from pydantic import Field


class ApprovalInput(FrozenModel):
    """request_approval 的输入。"""

    # task_id：字段说明见该模型文档与工作流契约。
    task_id: str
    # decision：字段说明见该模型文档与工作流契约。
    decision: Literal["PENDING", "APPROVE", "REJECT", "RETRY_LOCATE", "CONTINUE_REPAIR", "MARK_DONE", "TERMINATE"] = "PENDING"
    # mode：常规变更审批或定位失败后的人工处置。
    mode: Literal["change_approval", "manual_intervention"] = "change_approval"
    # intervention_reason：定位失败的可审计原因。
    intervention_reason: str = ""
    # intervention_step：触发人工介入的节点名称。
    intervention_step: str = "locate_agent"
    # candidate_files：当前定位阶段验证过的候选路径。
    candidate_files: list[str] = Field(default_factory=list)
    # root_cause：当前可复核的根因摘要。
    root_cause: str = ""
    # continue_allowed：只有定位的根因、源码证据与候选路径均有效时才允许继续修复。
    continue_allowed: bool = False
    # recommended_scope：范围建议。
    recommended_scope: Literal["carrier", "project", "manual"] = "manual"
    # carrier：字段说明见该模型文档与工作流契约。
    carrier: str
    # ai_workspace_path：字段说明见该模型文档与工作流契约。
    ai_workspace_path: str
    # changed_files：字段说明见该模型文档与工作流契约。
    changed_files: list[str]
    # baseline_manifest：字段说明见该模型文档与工作流契约。
    baseline_manifest: dict[str, str] = Field(default_factory=dict)
    # baseline_dir：字段说明见该模型文档与工作流契约。
    baseline_dir: str
    # authorized_files：字段说明见该模型文档与工作流契约。
    authorized_files: list[str] = Field(default_factory=list)
    # approval_fingerprint：字段说明见该模型文档与工作流契约。
    approval_fingerprint: str = ""
    # 最终审批拒绝时清理已经推送的修复分支。
    source_repo_path: str = ""
    repair_branch: str = ""
    delivery_pushed: bool = False


class ApprovalOutput(FrozenModel):
    """request_approval 的输出。"""

    # decision：字段说明见该模型文档与工作流契约。
    decision: Literal["PENDING", "APPROVE", "REJECT", "RETRY_LOCATE", "CONTINUE_REPAIR", "MARK_DONE", "TERMINATE"]
    # passed：字段说明见该模型文档与工作流契约。
    passed: bool = False
    # reason：字段说明见该模型文档与工作流契约。
    reason: str = ""


__all__ = ["ApprovalInput", "ApprovalOutput"]
