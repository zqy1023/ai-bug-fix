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
    decision: Literal["PENDING", "APPROVE", "REJECT"] = "PENDING"
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


class ApprovalOutput(FrozenModel):
    """request_approval 的输出。"""

    # decision：字段说明见该模型文档与工作流契约。
    decision: Literal["PENDING", "APPROVE", "REJECT"]
    # passed：字段说明见该模型文档与工作流契约。
    passed: bool = False
    # reason：字段说明见该模型文档与工作流契约。
    reason: str = ""


__all__ = ["ApprovalInput", "ApprovalOutput"]
