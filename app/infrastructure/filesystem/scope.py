"""落盘范围策略。

把"只允许改 carrier 内文件"这条硬规则从门禁和审批里抽出来，做成可替换的策略：
默认 carrier 层直接放行；project（共享层）必须由人工 ``APPROVE`` 才放行，
而且**只放行方案自己列出的那几个文件所在的目录**——批准一个方案不等于批准整仓。
共享层改动因此从"全部禁止"变成"按文件显式授权"。
"""

from __future__ import annotations

from pathlib import PurePosixPath

from app.application.plans import ChangePlan
from app.application.ports import ScopeDecision

APPROVE = "APPROVE"


class CarrierScopePolicy:
    """默认策略：carrier 内自动放行，跨目录方案必须逐文件人工授权。"""

    def decide(self, *, plan: ChangePlan, decision: str, carrier: str) -> ScopeDecision:
        """按方案影响面和人工决定给出放行范围。"""
        if plan.scope == "carrier":
            return ScopeDecision(
                allowed=True,
                tier="carrier",
                authorized=False,
                reason=f"目标 carrier 范围内修改: {carrier}",
                roots=(),
            )
        files = tuple(plan.paths)
        if not files:
            return ScopeDecision(
                allowed=False,
                tier="project",
                authorized=False,
                reason="scope_denied: 跨目录方案没有列出任何待改文件，无法授权",
                roots=(),
            )
        if not plan.requires_human_authorization:
            return ScopeDecision(
                allowed=False,
                tier="project",
                authorized=False,
                reason="scope_denied: 跨目录方案必须声明 requires_human_authorization=true，并等待人工审批",
                roots=(),
            )
        if decision != APPROVE:
            return ScopeDecision(
                allowed=False,
                tier="project",
                authorized=False,
                reason=f"scope_denied: 共享层方案尚未获人工批准（当前决定 {decision or 'PENDING'}）",
                roots=(),
            )
        # 只放行方案列出的文件所在目录；整仓 ("."、"") 永远不在授权结果里，
        # 否则一次批准就等于给后续所有文件开口子。
        return ScopeDecision(
            allowed=True,
            tier="project",
            authorized=True,
            reason=f"人工已批准共享层方案，放行 {len(files)} 个文件所在的目录",
            roots=_scopes_of(files),
        )


def _scopes_of(files: tuple[str, ...]) -> tuple[str, ...]:
    """取每个待改文件的父目录集合。文件在仓库根时用 ``"."``。"""
    scopes: list[str] = []
    for raw in files:
        normalized = str(raw).replace("\\", "/").strip()
        if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
            continue
        parent = PurePosixPath(normalized).parent.as_posix()
        scope = parent if parent not in {"", "/"} else "."
        if scope not in scopes:
            scopes.append(scope)
    return tuple(scopes)


__all__ = ["APPROVE", "CarrierScopePolicy"]