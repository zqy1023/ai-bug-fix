"""人工审批和最终门禁。"""

from __future__ import annotations

from typing import Any

from agents.review.gates.check_changes.verification import validate_changed_files


def final_gate(state: dict[str, Any]) -> dict[str, Any]:
    """人工确认保留后再次校验目标 carrier 范围。"""
    workspace = state.get("ai_workspace_path")
    if not workspace:
        return {"passed": False, "reason": "缺少最终门禁工作区"}
    return validate_changed_files(
        str(workspace),
        str(state.get("carrier", "")),
        list(state.get("changed_files", [])),
        state.get("baseline_manifest"),
        state.get("baseline_dir"),
        agent_policy=state.get("agent_policy"),
    )


__all__ = ["final_gate"]
