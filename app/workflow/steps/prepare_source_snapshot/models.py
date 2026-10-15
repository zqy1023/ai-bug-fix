"""源码快照步骤的输入输出契约。"""

from __future__ import annotations

from typing import Literal

from app.workflow.spec import FrozenModel


class SourceSnapshotInput(FrozenModel):
    """快照源仓库中目标 carrier 的确定性基线。"""

    task_id: str
    carrier: str
    source_repo_path: str


class SourceSnapshotOutput(FrozenModel):
    """修复前 carrier 文件清单与不可变基线目录。

    清单本体同时落 ``source_snapshot.json`` 产物，引用在步骤记录的信封里，不再在
    输出里另存一份路径字符串。
    """

    source_project_path: str
    framework: Literal["gathers", "spiders"]
    baseline_manifest: dict[str, str]
    baseline_dir: str
    file_count: int


__all__ = ["SourceSnapshotInput", "SourceSnapshotOutput"]
