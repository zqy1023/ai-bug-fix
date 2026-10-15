"""apply_plan：工作流唯一的方案落盘通道。

修复或审查给出结构化 ``ChangePlan``，这里按 ``ScopePolicy`` 判定影响面后写文件。
方案本身不直接改盘——审查 agent 恒只读、修复 agent 的越界写入会被门禁挡住，
落盘必须集中在这里，才谈得上"每个字节都有来源"。
"""

from __future__ import annotations

from pathlib import Path

from app.infrastructure.filesystem.scope import CarrierScopePolicy
from app.domain.errors import StepFailed
from app.application.context import StepContext
from app.application.ports import ScopeDecision
from app.workflow.steps.apply_plan.models import ApplyPlanInput, ApplyPlanOutput


def run(ctx: StepContext, data: ApplyPlanInput) -> ApplyPlanOutput:
    """按策略落盘方案；只处理共享层修复，carrier 修复不应该进入此步骤。"""
    plan = data.change_plan

    # 如果没有方案，直接通过（repair_agent 已在隔离工作区完成修改）
    if plan is None or not plan.edits:
        return ApplyPlanOutput(applied=False, reason="没有待落盘方案")

    # 判断影响范围
    decision = _decide(ctx, data, plan)

    # carrier 级别修复已经由 repair_agent 写进隔离工作区，这里不再落盘，
    # 否则会用方案内容覆盖修复结果，并把未审批的改动记成已授权文件。
    if decision.tier == "carrier":
        return ApplyPlanOutput(
            applied=False,
            reason="carrier 修复已由 repair_agent 完成，无需 apply_plan",
            scope="carrier",
        )

    # 共享层修复必须通过权限检查
    if not decision.allowed:
        raise StepFailed(decision.reason, {"applied": False, "reason": decision.reason}, result="rejected")

    workspace = Path(data.ai_workspace_path).resolve()
    if not workspace.is_dir():
        raise StepFailed("方案落盘失败：AI 工作区不存在", {"applied": False, "reason": "工作区不存在"})

    # 先验证整个计划，再进行任何写入。非法路径不能被静默跳过，
    # 否则会出现“部分落盘但报告成功”的不一致状态。
    resolved: list[tuple[FileEdit, Path]] = []
    seen: set[str] = set()
    for edit in plan.edits:
        target = _resolve(workspace, edit.path)
        key = target.as_posix().casefold()
        if key in seen:
            raise StepFailed(f"方案包含重复目标路径: {edit.path}", {"applied": False, "reason": "duplicate_path"})
        seen.add(key)
        if edit.action == "create" and target.exists():
            raise StepFailed(f"方案要求新建但文件已存在: {edit.path}", {"applied": False, "reason": "target_exists"})
        if edit.action == "update" and not target.exists():
            raise StepFailed(f"方案要求更新但文件不存在: {edit.path}", {"applied": False, "reason": "target_missing"})
        if edit.action == "delete" and not target.exists():
            # 删除已不存在的文件是幂等操作，无需写入。
            continue
        resolved.append((edit, target))

    written: list[str] = []
    for edit, target in resolved:
        if edit.action == "delete":
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(edit.content, encoding="utf-8")
        written.append(edit.path)

    return ApplyPlanOutput(
        applied=bool(written),
        written_files=written,
        skipped=[],
        authorized_files=sorted(written) if decision.tier == "project" else [],
        scope=decision.tier,
        reason=decision.reason,
    )


def _decide(ctx: StepContext, data: ApplyPlanInput, plan: ApplyPlan) -> ScopeDecision:
    """优先用装配注入的策略，单步调试时用默认实现。"""
    policy = getattr(ctx.services, "scope_policy", None) or CarrierScopePolicy()
    return policy.decide(plan=plan, decision=data.decision, carrier=data.carrier)


def _resolve(workspace: Path, relative: str) -> Path:
    """把方案里的相对路径解析到工作区内；非法路径直接拒绝整个方案。"""
    raw = str(relative or "").strip()
    relative_path = Path(raw)
    if not raw or relative_path.is_absolute() or any(part == ".." for part in relative_path.parts):
        raise StepFailed(f"方案路径必须是工作区内的相对路径: {relative!r}", {"applied": False, "reason": "invalid_path"})
    candidate = (workspace / relative_path).resolve(strict=False)
    try:
        candidate.relative_to(workspace.resolve())
    except ValueError as error:
        raise StepFailed(f"方案路径越出工作区: {relative!r}", {"applied": False, "reason": "path_escape"}) from error
    if candidate.exists() and candidate.is_dir():
        raise StepFailed(f"方案目标不能是目录: {relative!r}", {"applied": False, "reason": "target_is_directory"})
    return candidate


__all__ = ["run"]