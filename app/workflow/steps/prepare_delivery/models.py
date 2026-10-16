"""12 创建修复分支的输入输出契约。"""

from __future__ import annotations

from app.workflow.spec import FrozenModel


class DeliveryInput(FrozenModel):
    """11 的输入。

    ``error`` / ``repair`` / ``review`` 只用于拼提交说明：修复分支的commit
    带上"线上报的什么错、AI 怎么判、审查怎么评"，人工在 Git 上不用回查执行
    记录就能判断这次改动值不值。这里存文本结论，不重复整个上游输出。

    新增门禁字段：
    - review_decision: 审查结果（PASS/FAIL/MANUAL）
    - requires_approval: 是否需要人工审批
    - approval_granted: 人工审批是否已通过
    """

    # task_id：字段说明见该模型文档与工作流契约。
    task_id: str
    # carrier：字段说明见该模型文档与工作流契约。
    carrier: str
    # repair_branch：字段说明见该模型文档与工作流契约。
    repair_branch: str
    # source_repo_path：字段说明见该模型文档与工作流契约。
    source_repo_path: str = ""
    # ai_workspace_path：字段说明见该模型文档与工作流契约。
    ai_workspace_path: str
    # changed_files：字段说明见该模型文档与工作流契约。
    changed_files: list[str]
    # authorized_files：字段说明见该模型文档与工作流契约。
    authorized_files: list[str] = []
    # error：字段说明见该模型文档与工作流契约。
    error: str = ""
    # repair_analysis：字段说明见该模型文档与工作流契约。
    repair_analysis: str = ""
    # repair_summary：字段说明见该模型文档与工作流契约。
    repair_summary: str = ""
    # review_summary：字段说明见该模型文档与工作流契约。
    review_summary: str = ""
    review_decision: str = ""  # PASS/FAIL/MANUAL
    # requires_approval：字段说明见该模型文档与工作流契约。
    requires_approval: bool = False
    # approval_granted：字段说明见该模型文档与工作流契约。
    approval_granted: bool = False


class DeliveryOutput(FrozenModel):
    """11 的输出。"""

    # repair_branch：字段说明见该模型文档与工作流契约。
    repair_branch: str = ""
    # commit：字段说明见该模型文档与工作流契约。
    commit: str = ""
    # pushed：字段说明见该模型文档与工作流契约。
    pushed: bool = False


__all__ = ["DeliveryInput", "DeliveryOutput"]
