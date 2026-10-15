"""人工审批和最终门禁。"""

from __future__ import annotations

from typing import Any

from app.workflow.steps.check_changes.verification import validate_changed_files


def decision_of(state: dict[str, Any]) -> str:
    """读取人工决定，缺省为等待。"""
    value = state.get("human_decision")
    return value if isinstance(value, str) and value else "PENDING"


def final_gate(state: dict[str, Any]) -> dict[str, Any]:
    """人工批准后再次校验目标 carrier 范围。"""
    workspace = state.get("ai_workspace_path")
    if not workspace:
        return {"passed": False, "reason": "缺少最终门禁工作区"}
    return validate_changed_files(
        str(workspace),
        str(state.get("carrier", "")),
        list(state.get("changed_files", [])),
        state.get("baseline_manifest"),
        state.get("baseline_dir"),
        list(state.get("authorized_files", [])),
    )


__all__ = ["decision_of", "final_gate"]
