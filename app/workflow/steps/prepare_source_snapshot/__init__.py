"""为单个任务建立修复前源码基线。"""

from __future__ import annotations

from typing import Any

from app.workflow.spec import RecordView, StepSpec
from app.workflow.steps.prepare_source_snapshot.models import (
    SourceSnapshotInput,
    SourceSnapshotOutput,
)
from app.workflow.steps.prepare_source_snapshot.node import run


def _input(state: dict[str, Any]) -> dict[str, Any]:
    task = dict(state.get("task") or {})
    return {
        "task_id": str(state.get("task_id") or ""),
        "carrier": str(task.get("carrier") or ""),
        "source_repo_path": str(state.get("source_repo_path") or ""),
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_project_path": str(output["source_project_path"]),
        "framework": str(output["framework"]),
        "baseline_manifest": dict(output["baseline_manifest"]),
        "baseline_dir": str(output["baseline_dir"]),
    }


SPEC = StepSpec(
    name="prepare_source_snapshot",
    title="修复前源码快照",
    runner=run,
    input_model=SourceSnapshotInput,
    output_model=SourceSnapshotOutput,
    input_builder=_input,
    output_patch=_patch,
    # baseline_manifest 必须留在记录里：它是本步的正式输出字段，短路复用时
    # runner 要拿投影后的 output 重新过 SourceSnapshotOutput 校验，缺了就直接报错。
    output_record=RecordView(
        keep=(
            "source_project_path",
            "framework",
            "baseline_dir",
            "file_count",
            "baseline_manifest",
        )
    ),
)

__all__ = ["SPEC"]
