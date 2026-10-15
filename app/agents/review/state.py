"""审查子图状态。只在本目录使用。"""

from __future__ import annotations

from typing import Any, Literal

from typing_extensions import TypedDict


class ReviewState(TypedDict, total=False):
    """审查子图自己的状态。"""

    run_id: str
    task_id: str
    carrier: str
    biztype: str
    service_name: str
    framework: str
    problem: dict[str, Any]
    ai_workspace_path: str
    attempt: int
    repair: dict[str, Any]
    # 修复 agent 实际声明的相对路径（工作区相对路径，不是步骤说明）。
    changed_files: list[str]
    # 修改前的内容副本目录；审查用它与工作区比对，算出真实 diff 行。
    baseline_dir: str
    diff_summary: str
    gate_result: dict[str, Any]
    verify_result: dict[str, Any]
    regression_result: dict[str, Any]
    check_passed: bool
    verify_passed: bool
    regression_passed: bool
    verdict: Literal["PASS", "FAIL", "MANUAL"]
    passed: bool
    summary: str
    findings: list[str]
    required_changes: list[str]
    # 结构化回修方案必须声明，否则会被子图静默丢弃（与 RepairState.passed 同一陷阱）。
    change_plan: dict[str, Any]
    raw_result: str
    _usage: dict[str, Any]
    _audit: dict[str, Any]
