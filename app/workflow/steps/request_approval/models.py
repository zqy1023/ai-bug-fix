"""人工审批的输入输出契约。"""

from __future__ import annotations

from typing import Literal, get_args

from app.workflow.spec import FrozenModel
from pydantic import Field


RetryTarget = Literal[
    "locate_agent", "repair_agent", "validate_changes",
    "run_regression", "review_agent", "prepare_delivery",
]

# Single source of truth for retry-node validation in the API, node runner, and router.
APPROVAL_RETRY_TARGETS = frozenset(get_args(RetryTarget))


class ApprovalInput(FrozenModel):
    """request_approval 的输入。"""

    # task_id：字段说明见该模型文档与工作流契约。
    task_id: str
    # decision：字段说明见该模型文档与工作流契约。
    decision: Literal["PENDING", "APPROVE", "REJECT"] = "PENDING"
    # reason：审批说明；驳回时会写入节点输出和控制审计。
    reason: str = ""
    # retry_target：驳回后回退的工作流阶段；实际恢复值仍需在 node 内再次校验。
    retry_target: RetryTarget | None = None
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
    # approval_fingerprint：绑定交付物与最终门禁结果，防止过期审批。
    approval_fingerprint: str = ""
    # 最终审批拒绝时清理已经推送的修复分支。
    source_repo_path: str = ""
    repair_branch: str = ""
    delivery_pushed: bool = False
    # 未推送时本地修复分支同样需要在拒绝后清理。
    delivery_local_branch: bool = False


class ApprovalOutput(FrozenModel):
    """request_approval 的输出。"""

    # decision：字段说明见该模型文档与工作流契约。
    decision: Literal["PENDING", "APPROVE", "REJECT"]
    # passed：字段说明见该模型文档与工作流契约。
    passed: bool = False
    # reason：字段说明见该模型文档与工作流契约。
    reason: str = ""
    # retry_target：仅在驳回输出中回传给路由层的目标阶段。
    retry_target: str = ""


__all__ = ["APPROVAL_RETRY_TARGETS", "ApprovalInput", "ApprovalOutput", "RetryTarget"]
