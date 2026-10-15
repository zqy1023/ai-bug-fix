"""在 Agent 写入前保存目标 carrier 的确定性源码基线。"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

from app.application.context import StepContext
from app.domain.errors import StepFailed
from app.workflow.steps.prepare_source_snapshot.models import (
    SourceSnapshotInput,
    SourceSnapshotOutput,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(ctx: StepContext, data: SourceSnapshotInput) -> SourceSnapshotOutput:
    """从真实源仓库复制命中的 carrier 目录并记录普通文件哈希。"""
    if ctx.services.workspaces is None:
        raise StepFailed("工作区适配器未配置，无法解析源仓库")
    try:
        root = ctx.services.workspaces.project_root(data.source_repo_path)
    except Exception as error:
        raise StepFailed(f"源码快照源仓库无法读取: {error}") from error
    from app.infrastructure.filesystem.workspace import WorkspaceManager

    carrier = WorkspaceManager.safe_carrier_name(data.carrier)
    new_root = root / "gathers" / carrier
    if (new_root / "main.py").is_file():
        framework = "gathers"
        roots = [new_root.relative_to(root).as_posix()]
    elif (root / "spiders" / carrier).is_dir():
        framework = "spiders"
        roots = [(root / "spiders" / carrier).relative_to(root).as_posix()]
    else:
        raise StepFailed(f"源码快照找不到 carrier: {data.carrier}")

    task_dir = ctx.store.task_dir(ctx.run.run_id, data.task_id)
    task_dir.mkdir(parents=True, exist_ok=True)
    baseline = task_dir / "source_snapshot"
    if baseline.exists():
        raise StepFailed("源码快照目录已存在但没有通过记录，拒绝覆盖基线")
    temporary = task_dir / f".source_snapshot-{uuid.uuid4().hex}"
    manifest: dict[str, str] = {}
    try:
        temporary.mkdir(parents=True)
        for relative in roots:
            source_dir = (root / relative).resolve()
            try:
                source_dir.relative_to(root)
            except ValueError as error:
                raise StepFailed(f"carrier 路径越出工作区: {relative}") from error
            for source in source_dir.rglob("*"):
                if not source.is_file() or source.is_symlink():
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
        temporary.replace(baseline)
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
