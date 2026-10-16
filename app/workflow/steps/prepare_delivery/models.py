"""12 创建修复分支的输入输出契约。"""

from __future__ import annotations

from app.workflow.spec import FrozenModel


class DeliveryInput(FrozenModel):
    """11 的输入。

    ``error`` / ``repair`` / ``review`` 只用于拼提交说明：修复分支的commit
    带上"线上报的什么错、AI 怎么判、审查怎么评"，人工在 Git 上不用回查执行
    记录就能判断这次改动值不值。这里存文本结论，不重复整个上游输出。

    交付前门禁字段：
    - review_decision: 审查结果（PASS/FAIL/MANUAL）
    - authorized_files: 允许提交的 carrier 文件范围
    - analysis_only: 是否只交付 final.json 分析报告
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
    # analysis_only：只提交最终分析报告，不修改任何源码。
    analysis_only: bool = False
    # report_paths：AI 隔离工作区内需要随报告分支交付的审计产物。
    report_paths: list[str] = []


class DeliveryOutput(FrozenModel):
    """11 的输出。"""

    # repair_branch：字段说明见该模型文档与工作流契约。
    repair_branch: str = ""
    # commit：字段说明见该模型文档与工作流契约。
    commit: str = ""
    # pushed：字段说明见该模型文档与工作流契约。
    pushed: bool = False


__all__ = ["DeliveryInput", "DeliveryOutput"]
