"""跳过处置的唯一判定入口。

设计审查发现同一语义散在四处实现：

- ``workflow.engine.routes._analysis_only_task``（已无实际调用）
- ``core.policy.external_skip``
- ``core.task_outcome.task_outcome`` 里内联的 ``external_skip`` + ``judge`` 组合
- ``workflow.stages.finalize.node`` 里的 ``elif external_skip(...)``

任何一处改动都可能不同步。这里把三条规则集中定义，其余处只调用：

1. **平台确定性跳过**：策略关闭、旧框架只读边界、远端已有分支等，
   由平台自己产生，不需要模型审查。
2. **模型建议跳过**：模型提出 ``skip_reason`` 且归因为上游/环境，
   这只是提议，必须独立处置审查通过才能落地。
3. **只读分析交付**：无源码改动但有实质结论的分析报告。

三者不可混用：模型建议跳过被审查否决时必须回到修复或判失败，
不能因为落了终态标签就当成"已跳过"。
"""

from __future__ import annotations

from typing import Any

from core.policy import external_skip


def model_skip_proposal(payload: dict[str, Any] | None) -> bool:
    """模型是否提出了"无需本地修改"的跳过建议（尚未审查）。"""
    return external_skip(payload or {})


def skip_approved_by_review(payload: dict[str, Any] | None, review: dict[str, Any] | None) -> bool:
    """模型跳过建议是否已取得有效的独立处置审查通过。"""
    if not model_skip_proposal(payload):
        return False
    from agents.review.contract import judge

    checked = judge(review or {})
    return bool(checked.valid and checked.passed)


def deterministic_skip(report: dict[str, Any] | None, repair: dict[str, Any] | None) -> bool:
    """平台确定性跳过：没有模型跳过建议，但有明确的策略/边界原因。

    只有"根本没有模型跳过建议"才算确定性跳过；带着模型 skip_reason 的
    一律走审查路径，避免把"模型自己说不用改"混进平台结论。
    """
    if model_skip_proposal(repair) or model_skip_proposal(report):
        return False
    text = str((report or {}).get("skip_reason") or (repair or {}).get("skip_reason") or "").strip()
    return bool(text)


def unresolved_blocks_delivery(review: dict[str, Any] | None) -> bool:
    """补丁审查里的 unresolved_issues 阻断交付（分析处置不算）。"""
    payload = review or {}
    if payload.get("review_kind") == "analysis_disposition":
        return False
    return bool(payload.get("unresolved_issues"))


__all__ = [
    "deterministic_skip",
    "model_skip_proposal",
    "skip_approved_by_review",
    "unresolved_blocks_delivery",
]
