"""在 Agent 写入前保存目标 carrier 的确定性源码基线。"""

from __future__ import annotations

import hashlib
import shutil
import time
import uuid
from pathlib import Path

from app.application.context import StepContext
from app.domain.errors import StepFailed
from app.workflow.steps.context.prepare_source_snapshot.models import (
    SourceSnapshotInput,
    SourceSnapshotOutput,
)
from app.infrastructure.redaction import public_exception


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _publish_snapshot(temporary: Path, baseline: Path) -> None:
    """短暂目录占用可重试；绝不覆盖另一执行已发布的不可变基线。"""
    for attempt in range(3):
        if baseline.exists():
            raise StepFailed("源码基线已由其他执行创建，拒绝覆盖；请检查重复执行")
        try:
            temporary.rename(baseline)
            return
        except PermissionError as error:
            if attempt == 2:
                code = getattr(error, "winerror", None) or getattr(error, "errno", None)
                raise StepFailed(
                    f"源码快照发布失败：目录被占用或缺少重命名权限（系统错误码 {code or 'unknown'}）；请检查目标目录占用后重试。",
                    {"operation": "snapshot_publish", "exception_type": type(error).__name__, "os_error_code": code},
                ) from error
            time.sleep(0.1 * (attempt + 1))


def run(ctx: StepContext, data: SourceSnapshotInput) -> SourceSnapshotOutput:
    """从本轮 Git 干净树复制命中的 carrier 目录并记录普通文件哈希。"""
    if ctx.services.workspaces is None:
        raise StepFailed("工作区适配器未配置，无法解析源仓库")
    try:
        # 基线来自本轮唯一的 Git 干净树，未提交改动不进入 AI 隔离区。
        root = ctx.services.workspaces.prepare_run_tree(
            data.source_repo_path,
            ctx.run.run_id,
        )
    except Exception as error:
        raise StepFailed(public_exception(error, prefix="源码快照源仓库无法读取")) from error
    from app.infrastructure.filesystem.workspace import WorkspaceManager

    # 源码目录常带原始大小写（如 gathers/Airpak），必须按磁盘真实路径定位；
    # casefold 后的名字在 Linux 上找不到目录，也会让 manifest 与 Git 路径不一致。
    new_root = WorkspaceManager.find_carrier_dir(root, "gathers", data.carrier)
    legacy_root = WorkspaceManager.find_carrier_dir(root, "spiders", data.carrier)
    has_new = new_root is not None and (new_root / "main.py").is_file()
    has_legacy = legacy_root is not None
    if not has_new and not has_legacy:
        raise StepFailed(
            f"源码快照找不到 carrier（源码中可能已删除或重命名）: {data.carrier}",
            result="manual",
        )

    # 同时快照新旧框架的同名 carrier（如果两者都存在），以便只读定位能够核对
    # 新框架是否调用旧实现；是否允许修改由定位结论和后续范围门禁决定。
    framework = "gathers" if has_new else "spiders"
    from app.workflow.policy import AgentPolicy, resolve_policy
    meta = ctx.store.load_run(ctx.run.run_id)
    task_policy = (meta.get("task_policies") or {}).get(ctx.task.task_id, {})
    policy = AgentPolicy.model_validate(resolve_policy(meta.get("agent_policy"), task_policy))
    ctx.store.write_artifact(ctx.run.run_id, "policy/effective.json", {
        "values": policy.model_dump(mode="json"),
        "sources": {key: "task" if key in task_policy else "batch" if key in (meta.get("agent_policy") or {}) else "system" for key in policy.model_fields},
    }, data.task_id)
    roots = [
        path.relative_to(root).as_posix()
        for path, exists in ((new_root, has_new), (legacy_root, has_legacy))
        if exists and path is not None
    ]

    task_dir = ctx.store.task_dir(ctx.run.run_id, data.task_id)
    task_dir.mkdir(parents=True, exist_ok=True)
    baseline = task_dir / "source_snapshot"
    if baseline.exists():
        raise StepFailed("源码快照目录已存在但没有通过记录，拒绝覆盖基线")
    temporary = task_dir / f".source_snapshot-{uuid.uuid4().hex}"
    if policy.repair_shared:
        for relative in policy.shared_paths:
            target = root / relative
            if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()) or not target.exists():
                raise StepFailed(f"共享授权路径不存在或越界: {relative}")
            roots.append(relative)
    manifest: dict[str, str] = {}
    try:
        temporary.mkdir(parents=True)
        for relative in roots:
            source_dir = (root / relative).resolve()
            try:
                source_dir.relative_to(root)
            except ValueError as error:
                raise StepFailed(f"carrier 路径越出工作区: {relative}") from error
            for source in ([source_dir] if source_dir.is_file() else source_dir.rglob("*")):
                if not source.is_file() or source.is_symlink():
                    continue
                relative_parts = source.relative_to(source_dir).parts
                if (
                    any(
                        WorkspaceManager.is_skipped_dir(part) or WorkspaceManager.is_sensitive(part)
                        for part in relative_parts[:-1]
                    )
                    or WorkspaceManager.is_sensitive(source.name)
                ):
                    continue
                resolved = source.resolve()
                try:
                    resolved.relative_to(root)
                except ValueError as error:
                    raise StepFailed(f"carrier 文件越出工作区: {source}") from error
                workspace_relative = source.relative_to(root).as_posix()
                destination = temporary / workspace_relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                manifest[workspace_relative] = _sha256(source)
        if not manifest:
            raise StepFailed(f"carrier 没有可快照的普通文件: {data.carrier}")
        _publish_snapshot(temporary, baseline)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    ctx.emit_artifact("source_snapshot", "source_snapshot.json", manifest)
    return SourceSnapshotOutput(
        source_project_path=str(root),
        framework=framework,
        baseline_manifest=manifest,
        baseline_dir=str(baseline),
        file_count=len(manifest),
    )


__all__ = ["run"]

