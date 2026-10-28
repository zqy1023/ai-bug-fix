"""人工确认时仅检查交付身份及源码版本，不重新执行审查。"""
from __future__ import annotations
from typing import Any
from workflow.engine.handoff import evidence_version


def final_gate(state: dict[str, Any]) -> dict[str, Any]:
    if not state.get("candidate_id"):
        return {"passed": False, "reason": "缺少交付候选身份"}
    expected = state.get("evidence_version")
    if not expected:
        return {"passed": False, "reason": "缺少审批源码版本，请重新准备审批上下文"}
    current = evidence_version(str(state["ai_workspace_path"]), list(state.get("changed_files") or []), str(state.get("baseline_dir") or ""))
    return {"passed": current == expected, "reason": "" if current == expected else "审批期间源码变化，请重新审查并交付"}
