"""审查 agent 的输出契约与维度定义。

只读审查只输出结论、公开发现项和回修要求；包含数据契约、模型结构与审查维度校验。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypedDict

from pydantic import ConfigDict, Field

from config.settings import parse_bool
from core.contracts import ChangePlan, FrozenModel, RepairHandoff

RESULT_VALUES = ("PASS", "FAIL", "MANUAL")
REVIEW_CONTRACT_VERSION = 10
RESULT_FIELDS = (
    "verdict",
    "passed",
    "code_verdict",
    "coverage",
    "dimensions",
    "environment_assessment",
    "upstream_assessment",
    "summary",
    "findings",
    "required_changes",
    "todo",
    "unresolved_issues",
    "change_plan",
)
_MAX_FIELD_CHARS = 4000
_MAX_ITEMS = 20
READ_ONLY_TOOLS = ("Read", "Glob", "Grep")
ENV_PREFIX = "AI_REPAIR_AGENT_REVIEW_"

DEFAULTS: dict[str, Any] = {
    "description": "只读审查本轮修改",
    "enabled": True,
    "provider": "claude_session",
    "tools": list(READ_ONLY_TOOLS),
    "max_turns": 36,
    "max_tool_calls": 48,
    "max_context_chars": 60000,
    "max_candidate_files": 6,
    "max_diff_chars": 12000,
    "timeout_seconds": 900,
    "options": {},
}


class ReviewConfigError(ValueError):
    """审查 agent 配置非法。"""


@dataclass(frozen=True)
class ReviewConfig:
    """审查 agent 的运行配置。"""

    description: str = ""
    enabled: bool = True
    provider: str = "claude_session"
    tools: tuple[str, ...] = READ_ONLY_TOOLS
    max_turns: int = 36
    max_tool_calls: int = 48
    max_context_chars: int = 60000
    max_candidate_files: int = 6
    max_diff_chars: int = 12000
    timeout_seconds: int = 900
    options: Mapping[str, Any] = field(default_factory=dict)
    source: str = "defaults"

    @property
    def writable(self) -> bool:
        return False

    @classmethod
    def load(
        cls,
        path: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ReviewConfig:
        environment = dict(env if env is not None else os.environ)
        raw_path = str(path or environment.get(f"{ENV_PREFIX}CONFIG", "")).strip()
        default_path = Path(__file__).resolve().parent / "config.json"
        resolved = Path(raw_path) if raw_path else default_path
        if raw_path and not resolved.is_file():
            raise ReviewConfigError(f"指定的审查 Agent 配置不存在: {resolved}")
        payload = dict(DEFAULTS)
        source = "defaults"
        if resolved.is_file():
            try:
                loaded = json.loads(resolved.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    payload.update(loaded)
                    source = str(resolved)
            except (OSError, ValueError) as error:
                raise ReviewConfigError(f"审查 Agent 配置无效: {resolved}") from error
            if not isinstance(loaded, dict):
                raise ReviewConfigError(f"审查 Agent 配置必须为对象: {resolved}")
        enabled = environment.get(f"{ENV_PREFIX}ENABLED", "").strip()
        if enabled:
            payload["enabled"] = parse_bool(enabled)
        provider = environment.get(f"{ENV_PREFIX}PROVIDER", "").strip()
        if provider:
            payload["provider"] = provider
        tools_str = environment.get(f"{ENV_PREFIX}TOOLS", "").strip()
        if tools_str:
            payload["tools"] = [t.strip() for t in tools_str.split(",") if t.strip()]
        for int_field in (
            "max_turns", "max_tool_calls", "max_context_chars",
            "max_candidate_files", "max_diff_chars", "timeout_seconds"
        ):
            val = environment.get(f"{ENV_PREFIX}{int_field.upper()}", "").strip()
            if val:
                if not val.isdigit() or int(val) < 1:
                    raise ReviewConfigError(f"{ENV_PREFIX}{int_field.upper()} 必须为正整数")
                payload[int_field] = int(val)
            if not isinstance(payload[int_field], int) or isinstance(payload[int_field], bool) or payload[int_field] < 1:
                raise ReviewConfigError(f"{int_field} 必须为正整数")
        return cls(
            description=str(payload.get("description", "")),
            enabled=bool(payload.get("enabled", True)),
            provider=str(payload.get("provider", "claude_session")),
            tools=tuple(payload.get("tools", READ_ONLY_TOOLS)),
            max_turns=int(payload.get("max_turns", 24)),
            max_tool_calls=int(payload.get("max_tool_calls", 32)),
            max_context_chars=int(payload.get("max_context_chars", 18000)),
            max_candidate_files=int(payload.get("max_candidate_files", 6)),
            max_diff_chars=int(payload.get("max_diff_chars", 12000)),
            timeout_seconds=int(payload.get("timeout_seconds", 600)),
            options=payload.get("options", {}),
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "enabled": self.enabled,
            "provider": self.provider,
            "tools": list(self.tools),
            "max_turns": self.max_turns,
            "max_tool_calls": self.max_tool_calls,
            "max_context_chars": self.max_context_chars,
            "max_candidate_files": self.max_candidate_files,
            "max_diff_chars": self.max_diff_chars,
            "timeout_seconds": self.timeout_seconds,
            "options": dict(self.options),
            "source": self.source,
        }


def review_budget(config: ReviewConfig, files: list[str], diff_chars: int, semantic_risk: bool = False) -> dict[str, int]:
    """Scale review work to actual changes; explicit configuration stays a ceiling."""
    if not files or (len(files) == 1 and diff_chars <= 3000 and not semantic_risk):
        limits = (12, 16, 18000, 300)
    elif len(files) <= 3 and diff_chars <= 12000 and not semantic_risk:
        limits = (24, 32, 36000, 600)
    else:
        limits = (36, 48, 60000, 900)
    return {key: min(getattr(config, key), value) for key, value in zip(
        ("max_turns", "max_tool_calls", "max_context_chars", "timeout_seconds"), limits)}


class CoverageAssessment(FrozenModel):
    """审查针对实际变更判断覆盖是否足够，不能按 syntax_only 自动豁免。"""

    decision: Literal["sufficient", "insufficient", "unknown"] = "unknown"
    reason: str = ""
    reviewed_symbols: list[str] = Field(default_factory=list)
    missing_checks: list[str] = Field(default_factory=list)


class ReviewDimension(FrozenModel):
    status: Literal["passed", "failed", "unverified"] = "unverified"
    reason: str = ""
    evidence: list[str] = Field(default_factory=list)


class ReviewDimensions(FrozenModel):
    syntax: ReviewDimension = Field(default_factory=ReviewDimension)
    business: ReviewDimension = Field(default_factory=ReviewDimension)
    scope: ReviewDimension = Field(default_factory=ReviewDimension)


class ReviewAgentOutput(FrozenModel):
    """审查步骤对外只留结论、发现项和回修要求。"""

    verdict: Literal["PASS", "FAIL", "MANUAL"] = "FAIL"
    passed: bool = False
    code_verdict: Literal["PASS", "FAIL", "UNKNOWN"] = "UNKNOWN"
    coverage: CoverageAssessment = Field(default_factory=CoverageAssessment)
    dimensions: ReviewDimensions = Field(default_factory=ReviewDimensions)
    environment_assessment: str = ""
    upstream_assessment: str = ""
    summary: str = ""
    findings: list[str] = Field(default_factory=list)
    required_changes: list[str] = Field(default_factory=list, description="仅填写阻断当前补丁的必要修改或语义决策；code_verdict=PASS 时必须为空。非阻断回归建议写入 todo。")
    todo: list[str] = Field(default_factory=list, description="非阻断的后续回归建议，不影响代码审查或交付资格；真实代码缺陷和必要语义决策必须放入 findings/required_changes，不能移到此处绕过审查。")
    unresolved_issues: list[str] = Field(default_factory=list, description="分析交付或跳过后仍存在的已确认缺陷及必要业务决策，写明源码位置、证据和未处理原因。不是可选回归 TODO；有补丁时这些问题不能用于绕过阻断审查。")
    change_plan: ChangePlan | None = None
    review_contract_version: int = REVIEW_CONTRACT_VERSION


class ReviewInput(FrozenModel):
    """审查输入：只给审查需要的结论，不给修复侧内部状态。"""

    task_id: str
    ai_workspace_path: str = ""
    attempt: int = 1
    retry_context: dict[str, Any] = Field(default_factory=dict)
    repair: RepairHandoff = Field(default_factory=RepairHandoff)
    changed_files: list[str] = Field(default_factory=list)
    baseline_dir: str = ""
    diff_summary: str = ""


class ReviewView(ReviewAgentOutput):
    """对外展示视图，补充交付资格与审查清单。"""

    model_config = ConfigDict(extra="ignore")

    diff_files: list[dict[str, Any]] = Field(default_factory=list)
    delivery_eligible: bool = False
    semantic_verified: bool = False
    review_kind: Literal["patch", "analysis_disposition"] = "patch"
    disposition_verified: bool = False
    checks: list[dict[str, str]] = Field(default_factory=list)
    review_notes: str = ""
    contract_error: str = ""
    evidence_version: dict[str, Any] = Field(default_factory=dict)
    next_action: str = ""
    validation: dict[str, Any] = Field(default_factory=dict)
    review_contract_version: int = REVIEW_CONTRACT_VERSION


class ReviewState(TypedDict, total=False):
    """审查子图自己的状态。"""

    agent_policy: dict[str, Any]
    evidence: dict[str, Any]
    run_id: str
    source_project_path: str
    task_id: str
    carrier: str
    biztype: str
    service_name: str
    framework: str
    problem: dict[str, Any]
    ai_workspace_path: str
    attempt: int
    retry_context: dict[str, Any]
    repair: dict[str, Any]
    changed_files: list[str]
    baseline_dir: str
    diff_summary: str
    verdict: Literal["PASS", "FAIL", "MANUAL"]
    passed: bool
    delivery_eligible: bool
    semantic_verified: bool
    review_kind: Literal["patch", "analysis_disposition"]
    disposition_verified: bool
    review_contract_version: int
    contract_error: str
    evidence_version: dict[str, Any]
    diff_files: list[dict[str, Any]]
    checks: list[dict[str, str]]
    next_action: str
    review_notes: str
    code_verdict: str
    coverage: dict[str, Any]
    dimensions: dict[str, Any]
    environment_assessment: str
    upstream_assessment: str
    summary: str
    findings: list[str]
    required_changes: list[str]
    todo: list[str]
    unresolved_issues: list[str]
    change_plan: dict[str, Any]
    raw_result: str
    _usage: dict[str, Any]
    _audit: dict[str, Any]
    validation: dict[str, Any]
    _validation_report: dict[str, Any]


@dataclass(frozen=True)
class ReviewVerdict:
    """输出判定：结构是否合法、审查是否通过。"""

    valid: bool
    passed: bool
    reason: str = ""


def slim_review(payload: dict[str, Any]) -> dict[str, Any]:
    """裁剪成可持久化的审查结论视图，去掉无需长期携带的原始返回。"""
    slim: dict[str, Any] = {}
    for field_name in RESULT_FIELDS:
        if field_name not in payload:
            continue
        value = payload[field_name]
        if field_name == "change_plan" and isinstance(value, dict):
            from copy import deepcopy
            slim[field_name] = deepcopy(value)
        elif field_name in {"coverage", "dimensions"} and isinstance(value, dict):
            slim[field_name] = _clip_nested(value)
        elif isinstance(value, str):
            slim[field_name] = _clip(value)
        elif isinstance(value, list):
            slim[field_name] = _cap_items(value)
        else:
            slim[field_name] = value
    return slim


def _clip(value: str) -> str:
    return value if len(value) <= _MAX_FIELD_CHARS else f"{value[:_MAX_FIELD_CHARS]}…（已截断）"


def _clip_nested(value: Any) -> Any:
    if isinstance(value, str):
        return _clip(value)
    if isinstance(value, list):
        return [_clip_nested(item) for item in value[:_MAX_ITEMS]]
    if isinstance(value, dict):
        return {key: _clip_nested(item) for key, item in value.items()}
    return value


def _cap_items(items: list[Any]) -> list[Any]:
    capped: list[Any] = []
    for item in items[:_MAX_ITEMS]:
        if isinstance(item, str):
            capped.append(_clip(item))
        elif isinstance(item, dict):
            capped.append(
                {
                    str(key): (_clip(str(value)) if isinstance(value, str) else value)
                    for key, value in list(item.items())[:8]
                }
            )
        else:
            capped.append(item)
    return capped


def result_of(payload: dict[str, Any]) -> str:
    value = payload.get("verdict")
    return value.upper().strip() if isinstance(value, str) else ""


def validate_shape(payload: dict[str, Any]) -> tuple[bool, str]:
    value = result_of(payload)
    if "result" in payload:
        return False, "审查协议不接受 result 字段，必须使用 verdict"
    if value not in RESULT_VALUES:
        return False, f"verdict 必须是 {'/'.join(RESULT_VALUES)}"
    if not isinstance(payload.get("passed"), bool):
        return False, "passed 必须是布尔值"
    if (value == "PASS") != payload["passed"]:
        return False, "passed 必须与 verdict 一致"
    if not isinstance(payload.get("findings"), list):
        return False, "findings 必须是数组"
    if not isinstance(payload.get("required_changes"), list):
        return False, "required_changes 必须是数组"
    if not isinstance(payload.get("todo"), list):
        return False, "todo 必须是数组，用于非阻断回归建议"
    if payload.get("code_verdict") not in {"PASS", "FAIL", "UNKNOWN"}:
        return False, "code_verdict 必须独立声明 PASS/FAIL/UNKNOWN"
    if value == "PASS" and payload["code_verdict"] != "PASS":
        return False, "批准补丁必须先明确代码审查通过"
    for field_name in ("findings", "required_changes", "todo"):
        if any(not isinstance(item, str) or not item.strip() for item in payload[field_name]):
            return False, f"{field_name} 只能包含非空字符串"
    unresolved = payload.get("unresolved_issues", [])
    if not isinstance(unresolved, list) or any(not isinstance(item, str) or not item.strip() for item in unresolved):
        return False, "unresolved_issues 必须为非空字符串组成的数组"
    if payload.get("review_kind") == "patch" and unresolved and payload["code_verdict"] == "PASS":
        return False, "补丁存在未解决问题，不能用 unresolved_issues 绕过代码审查"
    if payload["code_verdict"] == "FAIL" and not payload["findings"]:
        return False, "代码缺陷结论必须提供具体 findings"
    if payload["code_verdict"] == "PASS" and payload["findings"]:
        return False, "已确认缺陷尚存在时不能声明代码检查通过"
    if payload["code_verdict"] == "PASS" and payload["required_changes"]:
        return False, "代码 PASS 不能同时包含必需修改；真实阻断项应判 FAIL/UNKNOWN，非阻断回归建议应写入 todo"
    if value == "PASS" and payload["required_changes"]:
        return False, "仍有必需处理项时不能批准补丁"
    dimensions = payload.get("dimensions")
    if not isinstance(dimensions, dict) or set(dimensions) != {"syntax", "business", "scope"}:
        return False, "必须提供语法、业务修复、影响范围三个审查维度"
    for key, item in dimensions.items():
        if not isinstance(item, dict) or item.get("status") not in {"passed", "failed", "unverified"}:
            return False, f"dimensions.{key} 状态无效"
        if not isinstance(item.get("reason"), str) or not item["reason"].strip():
            return False, f"dimensions.{key} 必须说明判断依据"
        refs = item.get("evidence")
        if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
            return False, f"dimensions.{key}.evidence 必须是证据引用数组"
        if item["status"] in {"passed", "failed"} and not refs:
            return False, f"dimensions.{key} 确定结论必须引用证据"
    statuses = {item["status"] for item in dimensions.values()}
    if value == "PASS" and statuses != {"passed"}:
        return False, "三个审查维度全部通过才能批准补丁"
    if "failed" in statuses and payload["code_verdict"] != "FAIL":
        return False, "确认审查缺陷时 code_verdict 必须为 FAIL"
    if payload["code_verdict"] == "FAIL" and "failed" not in statuses:
        return False, "代码缺陷必须对应具体审查维度"
    coverage = payload.get("coverage")
    if not isinstance(coverage, dict) or coverage.get("decision") not in {"sufficient", "insufficient", "unknown"}:
        return False, "coverage 必须明确声明 sufficient/insufficient/unknown"
    if not isinstance(coverage.get("reason"), str) or not coverage["reason"].strip():
        return False, "coverage 必须说明针对本次变更的覆盖依据或证据缺口"
    for field_name in ("reviewed_symbols", "missing_checks"):
        if not isinstance(coverage.get(field_name), list) or any(not isinstance(item, str) for item in coverage[field_name]):
            return False, f"coverage.{field_name} 必须是字符串数组"
    if payload["code_verdict"] == "PASS" and coverage["decision"] != "sufficient":
        return False, "代码审查通过必须明确实际变更覆盖充分"
    if coverage["decision"] == "sufficient" and coverage["missing_checks"]:
        return False, "覆盖仍有必需检查缺口时不能声明 sufficient"
    if payload.get("change_plan"):
        from pydantic import ValidationError

        from core.contracts import ChangePlan
        try:
            ChangePlan.model_validate(payload["change_plan"])
        except ValidationError as error:
            return False, f"change_plan 协议错误：{error}"
    return True, ""


def judge(payload: dict[str, Any]) -> ReviewVerdict:
    valid, reason = validate_shape(payload)
    if not valid:
        return ReviewVerdict(False, False, reason)
    value = result_of(payload)
    if value == "MANUAL":
        return ReviewVerdict(True, False, "审查 agent 要求转人工审查")
    if value != "PASS":
        return ReviewVerdict(True, False, f"审查未通过: {value}")
    return ReviewVerdict(True, True, "审查通过")


def delivery_allowed(payload: dict[str, Any]) -> bool:
    """仅放行有效通过，或代码已确认、仅业务证据不足的人工复核。"""
    checked = judge(payload)
    if not checked.valid or payload.get("contract_error"):
        return False
    if payload.get("review_kind") == "analysis_disposition":
        return checked.passed and payload.get("disposition_verified") is True
    if payload.get("unresolved_issues"):
        return False
    if checked.passed:
        return True
    dimensions = payload.get("dimensions") or {}
    return bool(
        result_of(payload) == "MANUAL"
        and payload.get("code_verdict") == "PASS"
        and (payload.get("coverage") or {}).get("decision") == "sufficient"
        and not (payload.get("coverage") or {}).get("missing_checks")
        and not payload.get("required_changes")
        and all((dimensions.get(key) or {}).get("status") == "passed" for key in ("syntax", "scope"))
        and (dimensions.get("business") or {}).get("status") in {"passed", "unverified"}
        and (payload.get("semantic_verified") is True or (dimensions.get("business") or {}).get("status") == "passed")
    )


def review_checks(
    state: dict[str, Any],
    code_verdict: str,
    coverage: dict[str, Any] | None = None,
    dimensions: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    dimensions = dimensions or {}
    rows = []
    labels = (("syntax", "语法检查"), ("business", "业务修复检查"), ("scope", "影响范围检查")) if state.get("changed_files") else (
        ("syntax", "源码与接口核对"), ("business", "分析与不修改依据"), ("scope", "权限与处置范围"))
    for key, label in labels:
        item = dimensions.get(key) or {}
        status = item.get("status", "unverified")
        detail = item.get("reason") or "等待修复后代码审查 Agent 核对证据"
        rows.append({"key": key, "label": label, "status": status, "detail": str(detail)[:4000]})
    return rows






__all__ = [
    "CoverageAssessment",
    "DEFAULTS",
    "ENV_PREFIX",
    "READ_ONLY_TOOLS",
    "RESULT_FIELDS",
    "RESULT_VALUES",
    "REVIEW_CONTRACT_VERSION",
    "ReviewAgentOutput",
    "ReviewConfig",
    "ReviewConfigError",
    "ReviewDimension",
    "ReviewDimensions",
    "ReviewInput",
    "ReviewState",
    "ReviewVerdict",
    "ReviewView",
    "judge",
    "result_of",
    "review_checks",
    "slim_review",
    "validate_shape",
]
