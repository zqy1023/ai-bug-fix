"""修复子图状态。只在本目录使用。"""

from __future__ import annotations

from typing import Any, Literal

from typing_extensions import TypedDict


class RepairState(TypedDict, total=False):
    """修复子图自己的状态。"""

    run_id: str
    task_id: str
    carrier: str
    biztype: str
    service_name: str
    problem_key: str
    problem: dict[str, Any]
    ai_workspace_path: str
    attempt: int
    retry_reason: str
    evidence: dict[str, Any]
    locate: dict[str, Any]
    analysis: str        # 模型给出的完整诊断结论
    summary: str         # 一句话标题式总结
    changed_files: list[str]
    change_plan: dict[str, Any]   # 结构化方案：passed=false 时同样保留
    blocked_reason: str          # 改不动的结构化原因
    scope_escalation_required: bool  # 需要人工授权跨目录落盘
    baseline_manifest: dict[str, str]
    baseline_dir: str
    result: Literal["passed", "failed"]
    passed: bool            # LangGraph 只保留声明过的通道，漏声明会被静默丢弃
    raw_result: str
    _usage: dict[str, Any]
    _audit: dict[str, Any]
