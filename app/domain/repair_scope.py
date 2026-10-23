"""统一决定旧框架只分析，避免定位、修复和交付各自扩大范围。"""

from __future__ import annotations

from typing import Any


def legacy_analysis_required(state: dict[str, Any]) -> bool:
    """依据确定性预检及明确的修改目标保留旧框架只读边界。"""
    evidence = state.get("evidence") or {}
    framework = evidence.get("framework") or state.get("framework")
    targets = [*(state.get("changed_files") or []),
               *((state.get("locate") or {}).get("proposed_edit_files") or [])]
    return (framework == "spiders" or evidence.get("legacy_only") is True
            or evidence.get("legacy_dependency") is True
            or any(str(path).replace("\\", "/").casefold().startswith("spiders/") for path in targets))
