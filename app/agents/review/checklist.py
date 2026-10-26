"""三个代码审查维度的展示投影。"""
from typing import Any


def review_checks(state: dict[str, Any], code_verdict: str,
                  coverage: dict[str, Any] | None = None,
                  dimensions: dict[str, Any] | None = None) -> list[dict[str, str]]:
    dimensions = dimensions or {}
    rows = []
    for key, label in (("syntax", "语法检查"), ("business", "业务修复检查"), ("scope", "影响范围检查")):
        item = dimensions.get(key) or {}
        status = item.get("status", "unverified")
        detail = item.get("reason") or "等待修复后代码审查 Agent 核对证据"
        if key == "syntax" and not state.get("syntax_passed"):
            status, detail = "unverified", "尚无通过的确定性语法检查，不能确认通过"
        if key == "scope" and not state.get("check_passed"):
            status, detail = "unverified", "修改范围门禁尚未通过，不能确认通过"
        rows.append({"key": key, "label": label, "status": status, "detail": str(detail)[:4000]})
    return rows
