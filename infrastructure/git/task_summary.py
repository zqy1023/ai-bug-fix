"""Render a readable task execution summary from the internal final report."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from infrastructure.storage.redaction import redact_for_delivery


def render_task_summary(report: Mapping[str, Any]) -> str:
    """Render recorded outcomes without claiming unexecuted validation passed."""
    labels = {
        "WAITING_APPROVAL": "待人工审批", "SUCCESS": "执行成功", "FAILED": "执行失败",
        "MANUAL": "需人工处理", "SKIPPED": "已跳过", "CANCELLED": "已停止",
        "PASS": "通过", "FAIL": "未通过", "verified": "已验证", "unverified": "业务未验证",
    }

    def text(value: Any, default: str = "未记录") -> str:
        raw = str(value).strip() if value is not None and str(value).strip() else default
        # task_summary.md 会提交到修复分支，属于交付面：problem/root_cause 等
        # 字段可能带回日志里的 URL 凭据、鉴权头，统一过交付脱敏后再渲染。
        # 内部落盘的 final.json（records、artifacts）不经过这里，仍保留原值。
        return redact_for_delivery(raw)

    outcome = text(report.get("result") or report.get("status"))
    lines = [
        "# 本轮任务执行概要", "",
        f"- 任务：`{text(report.get('task_id'))}`",
        f"- 运输商：{text(report.get('carrier'))}",
        f"- 业务类型：`{text(report.get('biztype'))}`",
        f"- 当前结果：{labels.get(outcome, outcome)}",
        f"- 执行耗时：{text(report.get('elapsed_ms'))} ms",
        f"- Token 用量：{text(report.get('tokens'))}", "",
        "## 问题与根因", "",
        text(report.get("problem")), "", text(report.get("root_cause")), "",
        "## 本轮修改", "", text(report.get("change_summary")), "",
    ]
    if report.get("repair_branch"):
        lines[8:8] = [f"- 交付分支：`{text(report['repair_branch'])}`"]
    lines[9:9] = ["", "此文档按交付前的执行记录生成；最终交付结果、提交号和总耗时以工作台终态记录为准。", ""]
    changed = report.get("changed_files") or []
    lines.extend(f"- `{text(path)}`" for path in changed)
    if not changed:
        lines.append("本轮没有业务源码修改。")
    lines.extend(["", "## 已执行步骤", ""])
    steps = report.get("completed_steps") or []
    lines.extend(f"- `{text(name)}`" for name in steps)
    if not steps:
        lines.append("未记录已完成的步骤。")
    lines.extend(["", "## 验证结果", ""])
    validation = report.get("validation") or {}
    if isinstance(validation, dict) and validation:
        for name, result in validation.items():
            if not isinstance(result, dict):
                continue
            status = result.get("status") or result.get("result")
            if not status:
                status = "PASS" if result.get("passed") is True else "FAIL" if result.get("passed") is False else "未记录结果"
            detail = result.get("reason") or result.get("summary")
            lines.append(f"- `{name}`：{labels.get(str(status), str(status))}"
                         + (f"。{text(detail)}" if detail else ""))
    else:
        lines.append("未记录验证结果，不能据此认定修复已验证通过。")
    if report.get("review_summary"):
        lines.extend(["", "审查结论：" + text(report["review_summary"])])
    lines.extend(["", "## 待确认事项与下一步", "", text(report.get("terminal_reason")), ""])
    for item in [*(report.get("evidence_gaps") or []), *(report.get("manual_confirmation_reasons") or [])]:
        lines.append(f"- {text(item)}")
    lines.extend(["", text(report.get("next_action")), ""])
    return "\n".join(lines)
