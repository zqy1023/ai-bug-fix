"""根因定位子图状态通道。"""

from __future__ import annotations

from typing import Any

from typing_extensions import TypedDict


class LocateState(TypedDict, total=False):
    """定位节点输入及其结构化结论、调用审计。"""

    # run_id：该模型字段用于承载对应契约中的结构化数据。
    run_id: str
    # task_id：该模型字段用于承载对应契约中的结构化数据。
    task_id: str
    # attempt：该模型字段用于承载对应契约中的结构化数据。
    attempt: int
    # carrier：该模型字段用于承载对应契约中的结构化数据。
    carrier: str
    # biztype：该模型字段用于承载对应契约中的结构化数据。
    biztype: str
    # service_name：该模型字段用于承载对应契约中的结构化数据。
    service_name: str
    # problem：该模型字段用于承载对应契约中的结构化数据。
    problem: dict[str, Any]
    # evidence：该模型字段用于承载对应契约中的结构化数据。
    evidence: dict[str, Any]
    # source_project_path：该模型字段用于承载对应契约中的结构化数据。
    source_project_path: str
    # source_snapshot：该模型字段用于承载对应契约中的结构化数据。
    source_snapshot: dict[str, Any]
    # status：该模型字段用于承载对应契约中的结构化数据。
    status: str
    # root_cause：该模型字段用于承载对应契约中的结构化数据。
    root_cause: str
    # category：该模型字段用于承载对应契约中的结构化数据。
    category: str
    # facts：该模型字段用于承载对应契约中的结构化数据。
    facts: list[str]
    # source_evidence：该模型字段用于承载对应契约中的结构化数据。
    source_evidence: list[dict[str, str]]
    # candidate_files：该模型字段用于承载对应契约中的结构化数据。
    candidate_files: list[str]
    # evidence_gaps：该模型字段用于承载对应契约中的结构化数据。
    evidence_gaps: list[str]
    # recommended_scope：该模型字段用于承载对应契约中的结构化数据。
    recommended_scope: str
    # summary：该模型字段用于承载对应契约中的结构化数据。
    summary: str
    # passed：该模型字段用于承载对应契约中的结构化数据。
    passed: bool
    # raw_result：该模型字段用于承载对应契约中的结构化数据。
    raw_result: str
    # _usage：该模型字段用于承载对应契约中的结构化数据。
    _usage: dict[str, Any]
    # _audit：该模型字段用于承载对应契约中的结构化数据。
    _audit: dict[str, Any]


__all__ = ["LocateState"]
