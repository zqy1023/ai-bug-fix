"""SDK output contracts prevent missing fields from becoming business conclusions."""

from typing import Any


def output_schema(role: str) -> dict[str, Any]:
    """Return a complete output contract with Chinese natural-language fields."""
    from app.agents.locate.models import LocateOutput
    from app.agents.repair.models import RepairAgentOutput
    from app.agents.review.models import ReviewAgentOutput

    model = {"locate": LocateOutput, "repair": RepairAgentOutput, "review": ReviewAgentOutput}[role]
    schema = model.model_json_schema()
    schema["required"] = list(schema["properties"])
    if role == "locate":
        evidence = schema.get("$defs", {}).get("SourceEvidence", {})
        for key in ("source_sha256", "start_line", "end_line", "snippet"):
            evidence.get("properties", {}).pop(key, None)
    for key in ("root_cause", "summary", "analysis", "blocked_reason"):
        if key in schema["properties"]:
            schema["properties"][key]["pattern"] = r"^$|[\u3400-\u9fff]"
    for key in ("facts", "evidence_gaps", "findings", "required_changes"):
        items = schema["properties"].get(key, {}).get("items", {})
        if items.get("type") == "string":
            items["pattern"] = r"[\u3400-\u9fff]"
    finding = schema.get("$defs", {}).get("SourceEvidence", {}).get("properties", {}).get("finding")
    if finding is not None:
        finding["pattern"] = r"[\u3400-\u9fff]"
    return schema
