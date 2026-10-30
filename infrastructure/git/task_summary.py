"""把内部 final.json 渲染成给人看的交付说明。

交付物是唯一的人机交接面：必须能独立读懂，而不是把内部字段直接打印出来。
按任务进度突出结论、建议、修改和验证，原始记录折叠展示，敏感凭据统一脱敏。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from infrastructure.storage.redaction import redact_for_delivery

_LABELS = {
    "WAITING_APPROVAL": "待人工审批", "SUCCESS": "执行成功", "FAILED": "执行失败",
    "MANUAL": "需人工处理", "SKIPPED": "已跳过", "CANCELLED": "已停止",
    "PASS": "通过", "FAIL": "未通过", "verified": "已验证", "unverified": "业务未验证",
    "passed": "通过", "failed": "未通过",
}
_ATTRIBUTION = {
    "code": "代码问题", "official": "官方问题", "environment": "环境配置问题",
    "mixed": "混合问题", "pending": "待判定",
}
_CATEGORY = {
    "carrier": "当前 carrier", "shared": "共享层", "downstream": "下游",
    "configuration": "配置", "missing_implementation": "实现缺失", "unknown": "未知",
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _items(value: Any) -> list[str]:
    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, (list, tuple)) else []


def _legacy(report: Mapping[str, Any]) -> bool:
    paths = _items(report.get("changed_files"))
    candidates = _items(report.get("candidate_files"))
    legacy_candidates_only = bool(candidates) and all(
        path.replace("\\", "/").casefold().startswith("spiders/") for path in candidates
    ) and report.get("framework") != "gathers" and not report.get("legacy_dependency")
    return bool(report.get("legacy_only") or report.get("framework") == "spiders" or legacy_candidates_only) or any(
        path.replace("\\", "/").casefold().startswith("spiders/") for path in paths
    )


def _phase(report: Mapping[str, Any]) -> tuple[str, str]:
    status = str(report.get("terminal_status") or report.get("result") or report.get("status") or "").upper()
    if status in {"FAILED", "FAIL", "BLOCKED", "CANCELLED", "SKIPPED", "REJECTED"}:
        return "stopped", {
            "FAILED": "执行失败", "FAIL": "执行失败", "BLOCKED": "任务受阻",
            "CANCELLED": "任务已停止", "SKIPPED": "任务已跳过", "REJECTED": "修复未获通过",
        }[status]
    analysis = (
        _legacy(report) or report.get("analysis_only")
        or str(report.get("locate_status") or "").upper() in {"ANALYSIS_ONLY", "INSUFFICIENT_EVIDENCE"}
        or status == "ANALYSIS_ONLY"
        or _mapping(report.get("resolution")).get("result") == "analysis_delivered"
        or _mapping(report.get("delivery")).get("delivery_phase") == "ANALYSIS_ONLY"
    )
    if analysis:
        return "analysis", "旧框架 · 分析交付" if _legacy(report) else "分析交付 · 待人工处理"
    if report.get("report_kind") == "repair_review":
        return "review", "修复已审查 · 待复核交付"
    if status == "SUCCESS":
        return "done", "修复交付完成"
    if _items(report.get("changed_files")) or status in {"PASS", "MANUAL", "WAITING_APPROVAL"}:
        return "review", "修复待复核" if _items(report.get("changed_files")) else "需人工处理"
    return "progress", "任务进行中"


def render_task_summary(report: Mapping[str, Any]) -> str:
    """按实际进度生成交接报告；建议、已修改与已验证分别陈述。"""
    report = dict(report)
    phase, title = _phase(report)
    problem = _mapping(report.get("problem"))
    changed = _items(report.get("changed_files"))
    root = str(report.get("root_cause") or report.get("analysis") or "").strip()
    lines = [f"# {title}", "", f"> {_text(report.get('terminal_reason'), '当前尚无终态结论。')}", "",
             "| 运输商 | 业务类型 | 当前结果 |", "| :--- | :--- | :--- |",
             f"| {_cell(report.get('carrier'))} | {_cell(report.get('biztype'))} | {_cell(_label(report.get('result') or report.get('status')))} |"]
    if report.get("repair_branch"):
        lines.extend(["", f"交付分支：`{_text(report['repair_branch'])}`"])

    lines.extend(["", "## 分析结论", "", _text(root, "尚未形成根因结论，需先补齐证据再决定修改方案。")])
    attribution = _ATTRIBUTION.get(str(report.get("attribution_type") or ""))
    category = _CATEGORY.get(str(report.get("category") or ""))
    if attribution or category:
        lines.extend(["", f"**责任归属**：{attribution or '待判定'} · **问题范围**：{category or '待判定'}"])
    facts = _items(report.get("facts"))
    if facts:
        lines.extend(["", "### 已确认事实", "", *[f"- {_text(item)}" for item in facts]])
    evidence = _evidence_lines(report.get("source_evidence"))
    if evidence:
        lines.extend(["", "### 定位依据", "", *evidence])

    rationale = _change_rationale(report.get("change_plan"))
    summary = str(report.get("change_summary") or "").strip()
    if phase == "analysis":
        lines.extend(["", "## 修改建议", "", "本轮仅交付分析，以下建议尚未实施。" if not changed else "记录中存在源码改动，需先核对其与只读分析范围是否一致。",
                      "", rationale or _text(summary, "尚无可执行的修改方案；请依据定位证据补充具体操作与验证方式，勿直接据此改动源码。")])
    elif changed:
        lines.extend(["", "## 已实施修改", "", *[f"- `{_text(path)}`" for path in changed]])
        if rationale or summary:
            lines.extend(["", rationale or _text(summary)])
    elif rationale or summary:
        lines.extend(["", "## 当前方案", "", "尚无业务源码修改记录。", "", rationale or _text(summary)])

    if phase in {"review", "done"} and changed:
        checks = _review_lines(report.get("review"))
        self_check = _self_check_line(report.get("self_check"))
        lines.extend(["", "## 验证与审查", ""])
        lines.extend(checks or ["尚无审查记录，不能据此认定业务修复已验证。"])
        if self_check:
            lines.extend(["", f"**自检**：{self_check}"])
        if report.get("review_summary"):
            lines.extend(["", _text(report["review_summary"])])

    review_todo = _items(_mapping(report.get("review")).get("todo"))
    if review_todo:
        lines.extend(["", "## TODO（非阻断回归建议）", "",
                      *["- [ ] " + _text(item).replace("\n", "\n      ") for item in dict.fromkeys(review_todo)]])
    todo = _todo_items(report)
    if phase == "analysis":
        actionable = _change_rationale(report.get("change_plan"))
        todo.insert(0, f"分析结论：{root}" if root else "补齐定位证据并形成明确的分析结论。")
        todo.insert(1, f"修改建议（尚未实施）：{actionable}" if actionable else
                    "补充具体修改建议：逐项说明目标文件、问题分支、修改操作及影响范围。")
        todo.append("由旧框架维护者按上述建议实施修改。" if _legacy(report) else "由对应模块负责人确认方案并实施修改。")
    criteria = _items(report.get("acceptance_criteria"))
    if todo or criteria:
        lines.extend(["", "## TODO（需人工确认）", ""])
        for item in dict.fromkeys(todo):
            lines.append("- [ ] " + _text(item).replace("\n", "\n      "))
        lines.extend(f"- [ ] 验收：{_text(item)}" for item in criteria)
        if phase == "analysis" and not criteria:
            lines.append("- [ ] 补充可重放的异常样本与正常样本，明确预期结果后执行回归。")
    if phase in {"stopped", "progress"}:
        lines.extend(["", "## 当前进度与阻塞", "",
                      _text(report.get("blocked_reason") or report.get("terminal_reason"), "尚无阻塞信息。")])
    if report.get("next_action"):
        lines.extend(["", "## 下一步", "", _text(report["next_action"])])

    lines.extend(["", "---", "", "<details>", "<summary>原始异常与执行记录</summary>", ""])
    request = _request_line(problem.get("req"))
    if request:
        lines.extend([f"请求：{request}", ""])
    error = _longest(problem.get("error"), problem.get("raw_error"), problem.get("message"),
                     report.get("problem") if isinstance(report.get("problem"), str) else "")
    if error:
        fence = "`" * max(3, max((len(part) for part in re.findall(r"`+", error)), default=0) + 1)
        lines.extend([fence + "text", error, fence, ""])
    response = str(problem.get("response") or "")
    lines.append(f"上游响应：已捕获 {len(response)} 字符，见工作台原始异常。" if response else "上游响应：未捕获，业务重放能力需单独确认。")
    steps = _items(report.get("completed_steps"))
    if steps:
        lines.extend(["", "已执行：" + " → ".join(f"`{_text(item)}`" for item in steps)])
    lines.extend(["", f"执行耗时：{_text(report.get('elapsed_ms'))} ms · Token 用量：{_text(report.get('tokens'))}",
                  "", "</details>", "", "此文档为交付时的执行快照；最终提交与任务终态以工作台记录为准。", ""])
    return "\n".join(lines)


def _cell(value: Any) -> str:
    return _text(value).replace("|", "&#124;").replace("\n", "<br>")


def _text(value: Any, default: str = "未记录") -> str:
    raw = str(value).strip() if value is not None and str(value).strip() else default
    # task_summary.md 会提交到修复分支，属于交付面：统一脱敏后再渲染。
    return redact_for_delivery(raw)


def _label(value: Any) -> str:
    raw = str(value or "").strip()
    return _LABELS.get(raw.upper(), _LABELS.get(raw, raw)) or "未记录"


def _longest(*values: Any) -> str:
    candidates = [str(value) for value in values if value]
    return max(candidates, key=len) if candidates else ""


def _request_line(raw: Any) -> str:
    """把请求 JSON 收成一行人话：单号 / 渠道 / 承运商。"""
    payload = raw
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return redact_for_delivery(raw.strip()[:200])
    if not isinstance(payload, dict):
        return ""
    parts: list[str] = []
    requests = payload.get("requests")
    if isinstance(requests, list) and requests:
        numbers = [
            str(item.get("number")) for item in requests[:3]
            if isinstance(item, dict) and item.get("number")
        ]
        if numbers:
            suffix = " 等" if len(requests) > 3 else ""
            parts.append("单号 " + "、".join(numbers) + suffix)
        channels = sorted({
            str(item.get("gather_channel")) for item in requests
            if isinstance(item, dict) and item.get("gather_channel")
        })
        if channels:
            parts.append("渠道 " + "、".join(channels))
    if payload.get("carrier"):
        parts.append(f"承运商 {payload['carrier']}")
    return redact_for_delivery("；".join(parts))


def _evidence_lines(value: Any) -> list[str]:
    rows: list[str] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        symbol = str(item.get("symbol") or "").strip()
        finding = str(item.get("finding") or "").strip()
        start, end = item.get("start_line"), item.get("end_line")
        where = f"{path}:{start}-{end}" if path and start and end else path
        if not where:
            continue
        head = f"`{redact_for_delivery(where)}`" + (f" · `{symbol}`" if symbol else "")
        rows.append(f"- {head}：{redact_for_delivery(finding)}" if finding else f"- {head}")
    return rows[:8]


def _change_rationale(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    rows: list[str] = []
    rationale = str(value.get("rationale") or "").strip()
    if rationale:
        rows.append(redact_for_delivery(rationale))
    edits: list[str] = []
    for edit in value.get("edits") or []:
        if not isinstance(edit, dict):
            continue
        path = str(edit.get("path") or "").strip()
        symbol = str(edit.get("symbol") or "").strip()
        details = [f"{label}：{_text(edit[key])}" for key, label in (
            ("problem", "问题"), ("operation", "操作"), ("note", "说明"),
            ("rationale", "原因"), ("validation_notes", "验证"), ("risk", "风险"),
        ) if str(edit.get(key) or "").strip()]
        notes = "；".join(details)
        if not (path or notes):
            continue
        head = f"`{redact_for_delivery(path)}`" + (f" · `{symbol}`" if symbol else "")
        edits.append(f"- {head}：{redact_for_delivery(notes)}" if notes else f"- {head}")
    if edits:
        if rows:
            rows.append("")
        rows.extend(edits)
    return "\n".join(rows)


def _self_check_line(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return ""
    valid = value.get("syntax_valid")
    notes = str(value.get("reflection_notes") or value.get("syntax_error") or "").strip()
    if valid is True:
        # 模型的 reflection_notes 常常只是"AST 自检通过"，与状态前缀重复，不再拼接。
        extra = f"。{redact_for_delivery(notes)}" if notes and "通过" not in notes else ""
        return "AST 语法自检通过" + extra
    if valid is False:
        return "AST 语法自检未通过" + (f"。{redact_for_delivery(notes)}" if notes else "")
    return "未记录自检结果" + (f"。{redact_for_delivery(notes)}" if notes else "")


def _review_lines(value: Any) -> list[str]:
    if not isinstance(value, dict) or not value:
        return []
    dimensions = value.get("dimensions") if isinstance(value.get("dimensions"), dict) else {}
    rows: list[str] = []
    for key, name in (("syntax", "语法"), ("business", "业务修复"), ("scope", "影响范围")):
        item = dimensions.get(key) if isinstance(dimensions.get(key), dict) else {}
        status = _label(item.get("status") or "unverified")
        reason = str(item.get("reason") or "").strip()
        rows.append(f"- {name}：{status}" + (f"。{redact_for_delivery(reason)}" if reason else ""))
    return rows


def _todo_items(report: Mapping[str, Any]) -> list[str]:
    """收集写进交付物的待办：业务未验证、证据缺口、显式人工理由。

    交付物是唯一的人机交接面，待办必须自带上下文——不能只留一句"需人工处理"。
    """
    items: list[str] = []

    def push(raw: Any) -> None:
        text = str(raw or "").strip()
        if text and text not in items:
            items.append(text)

    for raw in report.get("manual_confirmation_reasons") or ():
        push(raw)
    for raw in report.get("evidence_gaps") or ():
        push(raw)
    if _phase(report)[0] in {"analysis", "stopped", "progress"}:
        return items
    review = report.get("review") if isinstance(report.get("review"), dict) else {}
    verdict = str(report.get("review_decision") or review.get("verdict") or "").upper()
    if verdict == "MANUAL":
        covered = any(
            "业务未验证" in item or "unverified" in item.casefold() for item in items
        )
        if not covered:
            business = ((review.get("dimensions") or {}).get("business") or {})
            if str(business.get("status") or "").lower() == "unverified":
                push("业务回归未验证：缺少可重放的原始响应，需人工按真实单号核对本轮修复。")
            else:
                push("审查结论为 MANUAL：需人工确认业务影响后再合并。")
    elif report.get("manual_confirmation_required") and not items:
        push("核对本轮 diff 与执行记录，确认可以保留后再合并。")
    return items


__all__ = ["render_task_summary"]
