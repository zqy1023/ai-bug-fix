"""审查子图状态。只在本目录使用。"""

from __future__ import annotations

from typing import Any, Literal

from typing_extensions import TypedDict


class ReviewState(TypedDict, total=False):
    """审查子图自己的状态。"""

    # run_id：该模型字段用于承载对应契约中的结构化数据。
    run_id: str
    source_project_path: str
    # task_id：该模型字段用于承载对应契约中的结构化数据。
    task_id: str
    # carrier：该模型字段用于承载对应契约中的结构化数据。
    carrier: str
    # biztype：该模型字段用于承载对应契约中的结构化数据。
    biztype: str
    # service_name：该模型字段用于承载对应契约中的结构化数据。
    service_name: str
    # framework：该模型字段用于承载对应契约中的结构化数据。
    framework: str
    # problem：该模型字段用于承载对应契约中的结构化数据。
    problem: dict[str, Any]
    # ai_workspace_path：该模型字段用于承载对应契约中的结构化数据。
    ai_workspace_path: str
    # attempt：该模型字段用于承载对应契约中的结构化数据。
    attempt: int
    retry_context: dict[str, Any]
    # repair：该模型字段用于承载对应契约中的结构化数据。
    repair: dict[str, Any]
    # 修复 agent 实际声明的相对路径（工作区相对路径，不是步骤说明）。
    changed_files: list[str]
    # 修改前的内容副本目录；审查用它与工作区比对，算出真实 diff 行。
    baseline_dir: str
    # diff_summary：该模型字段用于承载对应契约中的结构化数据。
    diff_summary: str
    # gate_result：该模型字段用于承载对应契约中的结构化数据。
    gate_result: dict[str, Any]
    # verify_result：该模型字段用于承载对应契约中的结构化数据。
    verify_result: dict[str, Any]
    # regression_result：该模型字段用于承载对应契约中的结构化数据。
    regression_result: dict[str, Any]
    # check_passed：该模型字段用于承载对应契约中的结构化数据。
    check_passed: bool
    syntax_passed: bool
    # verify_passed：该模型字段用于承载对应契约中的结构化数据。
    verify_passed: bool
    business_verification_status: str
    # regression_passed：该模型字段用于承载对应契约中的结构化数据。
    regression_passed: bool
    # verdict：该模型字段用于承载对应契约中的结构化数据。
    verdict: Literal["PASS", "FAIL", "MANUAL"]
    # passed：该模型字段用于承载对应契约中的结构化数据。
    passed: bool
    delivery_eligible: bool
    review_contract_version: int
    contract_error: str
    evidence_version: dict[str, Any]
    checks: list[dict[str, str]]
    validation_summary: dict[str, Any]
    next_action: str
    review_notes: str
    code_verdict: str
    coverage: dict[str, Any]
    # summary：该模型字段用于承载对应契约中的结构化数据。
    summary: str
    # findings：该模型字段用于承载对应契约中的结构化数据。
    findings: list[str]
    # required_changes：该模型字段用于承载对应契约中的结构化数据。
    required_changes: list[str]
    # 每条验收项的实际观察、证据引用与 patch 版本。
    acceptance_results: list[dict[str, Any]]
    # 结构化回修方案必须声明，否则会被子图静默丢弃（与 RepairState.passed 同一陷阱）。
    change_plan: dict[str, Any]
    # raw_result：该模型字段用于承载对应契约中的结构化数据。
    raw_result: str
    # _usage：该模型字段用于承载对应契约中的结构化数据。
    _usage: dict[str, Any]
    # _audit：该模型字段用于承载对应契约中的结构化数据。
    _audit: dict[str, Any]
