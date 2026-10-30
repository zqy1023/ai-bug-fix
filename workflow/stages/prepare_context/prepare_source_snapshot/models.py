"""源码快照步骤的输入输出契约。"""

from __future__ import annotations

from typing import Any, Literal
from pydantic import Field

from core.contracts import FrozenModel


class SourceSnapshotInput(FrozenModel):
    """快照源仓库中目标 carrier 的确定性基线。"""

    # task_id：字段说明见该模型文档与工作流契约。
    task_id: str
    # carrier：字段说明见该模型文档与工作流契约。
    carrier: str
    # source_repo_path：字段说明见该模型文档与工作流契约。
    source_repo_path: str


class SourceSnapshotOutput(FrozenModel):
    """修复前 carrier 文件清单与不可变基线目录。

    清单本体同时落 ``source_snapshot.json`` 产物，引用在步骤记录的信封里，不再在
    输出里另存一份路径字符串。
    """

    # source_project_path：字段说明见该模型文档与工作流契约。
    source_project_path: str
    # framework：字段说明见该模型文档与工作流契约。
    framework: Literal["gathers", "spiders"]
    # baseline_manifest：字段说明见该模型文档与工作流契约。
    baseline_manifest: dict[str, str]
    # baseline_dir：字段说明见该模型文档与工作流契约。
    baseline_dir: str
    # file_count：字段说明见该模型文档与工作流契约。
    file_count: int
    agent_policy: dict[str, Any] = Field(default_factory=dict)
    policy_sources: dict[str, str] = Field(default_factory=dict)


__all__ = ["SourceSnapshotInput", "SourceSnapshotOutput"]
