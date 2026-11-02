"""SDK 输出契约：只强制"决定结论"的字段，其余留默认值。

此前把模型类的**全部**属性都塞进 ``required``，SDK 的 json_schema 强校验会迫使
模型为每个字段编一个值（哪怕空字符串），既多花输出 token，又诱导
"facts/evidence_gaps/acceptance_criteria 里凑条目"的填空式幻觉。

现在的划分原则：

- **required**：缺了会让下游判错或无法审计的字段。平台读它做路由
  （``status``/``passed``/``result``/``skip_reason``）、做归因
  （``attribution_type``）、或审查要靠它复核（``root_cause``/``summary``/
  ``analysis``/``acceptance_criteria``）。这些必须给出真实值，因此保留非空 pattern。
- **optional**：有 pydantic 默认值、缺失等价于"本轮不适用"的字段
  （``candidate_files``/``change_plan``/``self_check``/``evidence_gaps`` 等）。
  模型不必为空数组编内容，平台侧读取本来就用 ``or []`` 兜底。

审查 Agent 的字段全部保留 required：``validate_shape`` 逐项校验
``verdict``/``passed``/``code_verdict``/``coverage``/``dimensions``/``summary``/
``findings``/``required_changes``/``todo``/``unresolved_issues``，缺任何一项都会被判
协议错误，反而更贵。
"""

from typing import Any

# 修复 Agent：只强制决定结论与审查交接必需的字段。
_UNIFIED_REQUIRED: tuple[str, ...] = (
    "status",
    "attribution_type",
    "root_cause",
    "summary",
    "analysis",
    "acceptance_criteria",
    "changed_files",
    "skip_reason",
    "passed",
    "result",
)

# 这些字段必须非空：空值会让报告/审查拿到"看起来有值、实际没内容"的结论。
_NON_EMPTY_TEXT: tuple[str, ...] = ("root_cause", "summary", "analysis")


def output_schema(role: str) -> dict[str, Any]:
    """返回当前角色的输出 JSON Schema。"""
    from agents.review.contract import RESULT_FIELDS, ReviewAgentOutput
    from agents.unified_fixer.models import UnifiedFixerOutput

    model = {"unified_fixer": UnifiedFixerOutput, "review": ReviewAgentOutput}[role]
    schema = model.model_json_schema()
    properties = schema.get("properties", {})

    if role == "unified_fixer":
        # 只把真正需要的字段设为必需，其余交给 pydantic 默认值。
        schema["required"] = [name for name in _UNIFIED_REQUIRED if name in properties]
        evidence = schema.get("$defs", {}).get("SourceEvidence", {})
        for key in ("source_sha256", "start_line", "end_line", "snippet"):
            evidence.get("properties", {}).pop(key, None)
    else:
        schema["required"] = [name for name in RESULT_FIELDS if name in properties]

    for key in _NON_EMPTY_TEXT:
        if key in properties:
            properties[key]["pattern"] = r"\S"
    for key in ("facts", "evidence_gaps", "findings", "required_changes", "todo", "unresolved_issues"):
        items = properties.get(key, {}).get("items", {})
        if items.get("type") == "string":
            items["pattern"] = r"\S"
    finding = schema.get("$defs", {}).get("SourceEvidence", {}).get("properties", {}).get("finding")
    if finding is not None:
        finding["pattern"] = r"\S"
    return schema


__all__ = ["output_schema"]
