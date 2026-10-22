"""修复子图状态。只在本目录使用。"""

from __future__ import annotations

from typing import Any, Literal

from typing_extensions import TypedDict


class RepairState(TypedDict, total=False):
    """修复子图自己的状态。"""

    # run_id：该模型字段用于承载对应契约中的结构化数据。
    run_id: str
    # task_id：该模型字段用于承载对应契约中的结构化数据。
    task_id: str
    # carrier：该模型字段用于承载对应契约中的结构化数据。
    carrier: str
    # biztype：该模型字段用于承载对应契约中的结构化数据。
    biztype: str
    # service_name：该模型字段用于承载对应契约中的结构化数据。
    service_name: str
    # problem_key：该模型字段用于承载对应契约中的结构化数据。
    problem_key: str
    # problem：该模型字段用于承载对应契约中的结构化数据。
    problem: dict[str, Any]
    # ai_workspace_path：该模型字段用于承载对应契约中的结构化数据。
    ai_workspace_path: str
    # source_project_path：本轮共享只读源码树，用于读取框架和公共库。
    source_project_path: str
    # attempt：该模型字段用于承载对应契约中的结构化数据。
    attempt: int
    # retry_reason：该模型字段用于承载对应契约中的结构化数据。
    retry_reason: str
    retry_context: dict[str, Any]
    # evidence：该模型字段用于承载对应契约中的结构化数据。
    evidence: dict[str, Any]
    # locate：该模型字段用于承载对应契约中的结构化数据。
    locate: dict[str, Any]
    analysis: str        # 模型给出的完整诊断结论
    summary: str         # 一句话标题式总结
    # changed_files：该模型字段用于承载对应契约中的结构化数据。
    changed_files: list[str]
    change_plan: dict[str, Any]   # 结构化方案：passed=false 时同样保留
    blocked_reason: str          # 改不动的结构化原因
    scope_escalation_required: bool  # 共享层范围，只能转分析报告
    # baseline_manifest：该模型字段用于承载对应契约中的结构化数据。
    baseline_manifest: dict[str, str]
    # baseline_dir：该模型字段用于承载对应契约中的结构化数据。
    baseline_dir: str
    # result：该模型字段用于承载对应契约中的结构化数据。
    result: Literal["passed", "failed"]
    passed: bool            # LangGraph 只保留声明过的通道，漏声明会被静默丢弃
    # raw_result：该模型字段用于承载对应契约中的结构化数据。
    raw_result: str
    # _usage：该模型字段用于承载对应契约中的结构化数据。
    _usage: dict[str, Any]
    # _audit：该模型字段用于承载对应契约中的结构化数据。
    _audit: dict[str, Any]
