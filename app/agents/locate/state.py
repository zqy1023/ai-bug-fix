"""根因定位子图状态通道。"""

from __future__ import annotations

from typing import Any

from typing_extensions import TypedDict


class LocateState(TypedDict, total=False):
    """定位节点输入及其结构化结论、调用审计。"""

    run_id: str
    task_id: str
    attempt: int
    carrier: str
    biztype: str
    service_name: str
    problem: dict[str, Any]
    evidence: dict[str, Any]
    source_project_path: str
    source_snapshot: dict[str, Any]
    status: str
    root_cause: str
    category: str
    facts: list[str]
    source_evidence: list[dict[str, str]]
    candidate_files: list[str]
    evidence_gaps: list[str]
    recommended_scope: str
    summary: str
    passed: bool
    raw_result: str
    _usage: dict[str, Any]
    _audit: dict[str, Any]


__all__ = ["LocateState"]
