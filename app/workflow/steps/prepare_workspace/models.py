"""任务级隔离工作区的输入输出契约。"""

from __future__ import annotations

from app.workflow.spec import FrozenModel


class WorkspaceInput(FrozenModel):
    """创建指定运行任务的隔离工作区。"""

    run_id: str
    task_id: str
    source_repo_path: str
    source_project_path: str
    baseline_dir: str
    carrier: str
    framework: str


class WorkspaceOutput(FrozenModel):
    """隔离工作区路径。"""

    ai_workspace_path: str


__all__ = ["WorkspaceInput", "WorkspaceOutput"]
