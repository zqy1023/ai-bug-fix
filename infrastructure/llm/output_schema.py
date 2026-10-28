"""SDK output contracts prevent missing fields from becoming business conclusions."""

from typing import Any


def output_schema(role: str) -> dict[str, Any]:
    """Return a complete output contract with Chinese natural-language fields."""
    from agents.unified_fixer.models import UnifiedFixerOutput
    from agents.review.contract import ReviewAgentOutput

    model = {"unified_fixer": UnifiedFixerOutput, "review": ReviewAgentOutput}[role]
    schema = model.model_json_schema()
    schema["required"] = list(schema["properties"])
    if role == "unified_fixer":
        evidence = schema.get("$defs", {}).get("SourceEvidence", {})
        for key in ("source_sha256", "start_line", "end_line", "snippet"):
            evidence.get("properties", {}).pop(key, None)
    for key in ("root_cause", "summary", "analysis", "blocked_reason"):
        if key in schema["properties"]:
            schema["properties"][key]["pattern"] = r"\S"
    for key in ("facts", "evidence_gaps", "findings", "required_changes"):
        items = schema["properties"].get(key, {}).get("items", {})
        if items.get("type") == "string":
            items["pattern"] = r"\S"
    finding = schema.get("$defs", {}).get("SourceEvidence", {}).get("properties", {}).get("finding")
    if finding is not None:
        finding["pattern"] = r"\S"
    return schema


__all__ = ["output_schema"]
