"""为单个任务建立修复前源码基线。"""

from __future__ import annotations

from typing import Any

from app.workflow.spec import RecordView, StepSpec
from app.workflow.steps.context.prepare_source_snapshot.models import (
    SourceSnapshotInput,
    SourceSnapshotOutput,
)
from app.workflow.steps.context.prepare_source_snapshot.node import run


def _input(state: dict[str, Any]) -> dict[str, Any]:
    task = dict(state.get("task") or {})
    return {
        "task_id": str(state.get("task_id") or ""),
        "carrier": str(task.get("carrier") or ""),
        "source_repo_path": str(state.get("source_repo_path") or ""),
    }


def _patch(output: dict[str, Any]) -> dict[str, Any]:
    # 步骤没有产出快照（被取消或执行失败）：不回写任何状态。
    if "source_project_path" not in output:
        return {}
    return {
        "source_project_path": str(output.get("source_project_path") or ""),
        "framework": str(output.get("framework") or ""),
        "baseline_manifest": dict(output.get("baseline_manifest") or {}),
        "baseline_dir": str(output.get("baseline_dir") or ""),
    }


SPEC = StepSpec(
    name="prepare_source_snapshot",
    title="修复前源码快照",
    runner=run,
    input_model=SourceSnapshotInput,
    output_model=SourceSnapshotOutput,
    input_builder=_input,
    output_patch=_patch,
    output_record=RecordView(
        # baseline_manifest 不进步骤记录：它是"carrier 全部文件 × 哈希"的全量清单，
        # 体积随 carrier 规模线性增长，整份留在 steps.json 里会顶破记录大小上限。
        # 清单本体已经是 source_snapshot.json 产物，状态通道与交接产物里也各有一份，
        # 单步续跑从 workflow/state.json 恢复，不依赖步骤记录里的副本。
        keep=(
            "source_project_path",
            "framework",
            "baseline_dir",
            "file_count",
        )
    ),
)

__all__ = ["SPEC"]

