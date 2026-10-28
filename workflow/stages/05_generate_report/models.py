"""创建修复分支的输入输出契约。"""

from __future__ import annotations

from pydantic import Field

from core.contracts import EvidenceBinding
from core.contracts import FrozenModel


class DeliveryInput(FrozenModel):
    agent_policy: dict = Field(default_factory=dict)
    """交付步骤的输入。

    ``error`` / ``repair`` / ``review`` 只用于拼提交说明：修复分支的commit
    带上"线上报的什么错、AI 怎么判、审查怎么评"，人工在 Git 上不用回查执行
    记录就能判断这次改动值不值。这里存文本结论，不重复整个上游输出。

    交付前门禁字段：
    - review_decision: 审查结果（PASS/FAIL/MANUAL）
    - analysis_only: 是否只交付 final.json 分析报告
    - allow_push: 是否允许推送远程修复分支
    - require_push: 是否要求远程存在且推送成功
    - publish: 是否正式交付；交付后由人工复核决定保留或打回
    """

    # task_id：字段说明见该模型文档与工作流契约。
    publish: bool = False
    # delivery_candidate_id：准备阶段生成的候选身份，正式交付必须交付同一候选。
    delivery_candidate_id: str = ""
    binding: EvidenceBinding | None = None
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
    # error：字段说明见该模型文档与工作流契约。
    error: str = ""
    # repair_analysis：字段说明见该模型文档与工作流契约。
    repair_analysis: str = ""
    # repair_summary：字段说明见该模型文档与工作流契约。
    repair_summary: str = ""
    # review_summary：字段说明见该模型文档与工作流契约。
    review_summary: str = ""
    review_decision: str = ""  # PASS/FAIL/MANUAL
    delivery_eligible: bool = False
    legacy_analysis_required: bool = False
    # analysis_only：只提交最终分析报告，不修改任何源码。
    analysis_only: bool = False
    # report_paths：AI 隔离工作区内需要随报告分支交付的审计产物。
    report_paths: list[str] = []
    # allow_push：由运行配置决定是否允许远程推送。
    allow_push: bool = False
    # require_push：只有明确要求时才把缺少远程视为交付失败。
    require_push: bool = False
    manual_confirmation_required: bool = False
    manual_confirmation_reasons: list[str] = Field(default_factory=list)


class DeliveryOutput(FrozenModel):
    candidate_id: str = ""
    binding: EvidenceBinding | None = None
    delivery_status: str = "PREPARED"
    """交付步骤的输出。"""

    # repair_branch：字段说明见该模型文档与工作流契约。
    repair_branch: str = ""
    # commit：字段说明见该模型文档与工作流契约。
    commit: str = ""
    # pushed：字段说明见该模型文档与工作流契约。
    pushed: bool = False
    # local_branch：未推送时提交已写回源仓库的本地修复分支。
    local_branch: bool = False
    manual_confirmation_required: bool = False
    manual_confirmation_reasons: list[str] = Field(default_factory=list)


__all__ = ["DeliveryInput", "DeliveryOutput"]
