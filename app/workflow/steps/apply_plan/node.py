"""apply_plan：工作流唯一的方案落盘通道。

修复或审查给出结构化 ``ChangePlan``，这里按 ``ScopePolicy`` 判定影响面后写文件。
方案本身不直接改盘——审查 agent 恒只读、修复 agent 的越界写入会被门禁挡住，
落盘必须集中在这里，才谈得上"每个字节都有来源"。
"""

from __future__ import annotations

from pathlib import Path
import os
import tempfile

from app.application.plans import ChangePlan, FileEdit

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

    # 先把所有新内容写入同目录临时文件，再统一替换；中途失败时恢复备份。
    staged: list[tuple[FileEdit, Path, Path | None]] = []
    backups: dict[Path, bytes | None] = {}
    written: list[str] = []
    try:
        for edit, target in resolved:
            backups[target] = target.read_bytes() if target.exists() else None
            if edit.action == "delete":
                staged.append((edit, target, None))
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".stage", dir=target.parent)
            temporary = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                stream.write(edit.content)
                stream.flush()
                os.fsync(stream.fileno())
            staged.append((edit, target, temporary))

        for edit, target, temporary in staged:
            if edit.action == "delete":
                target.unlink(missing_ok=True)
            else:
                assert temporary is not None
                os.replace(temporary, target)
            written.append(edit.path)
    except Exception as error:
        for target, content in reversed(list(backups.items())):
            try:
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".rollback", dir=target.parent)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(name, target)
            except OSError:
                raise StepFailed(
                    f"方案写入失败且回滚失败，工作区需要人工恢复: {target}",
                    {"applied": False, "reason": "rollback_failed"},
                ) from error
        raise StepFailed(
            f"方案写入失败，已尝试回滚: {error}",
            {"applied": False, "reason": "write_failed"},
        ) from error
    finally:
        for _, _, temporary in staged:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

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
    unresolved = workspace / relative_path
    current = workspace.resolve()
    for part in relative_path.parts:
        if part in {"", "."}:
            continue
        current = current / part
        if current.is_symlink():
            raise StepFailed(
                f"方案路径不得经过符号链接: {relative!r}",
                {"applied": False, "reason": "symlink_path"},
            )
    candidate = unresolved.resolve(strict=False)
    try:
        candidate.relative_to(workspace.resolve())
    except ValueError as error:
        raise StepFailed(f"方案路径越出工作区: {relative!r}", {"applied": False, "reason": "path_escape"}) from error
    if candidate.exists() and candidate.is_dir():
        raise StepFailed(f"方案目标不能是目录: {relative!r}", {"applied": False, "reason": "target_is_directory"})
    return candidate


__all__ = ["run"]