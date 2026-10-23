"""人工审查义务仅由统一决策产生。"""
from typing import Any

def confirmation(state: dict[str, Any]) -> tuple[bool, list[str]]:
    decision = state.get("validation_decision") or {}
    required = decision.get("manual_required") is True
    return required, ["用户选择人工代码审查"] if required else []
