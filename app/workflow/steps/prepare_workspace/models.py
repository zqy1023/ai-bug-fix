"""任务级隔离工作区的输入输出契约。"""

from __future__ import annotations

from app.workflow.spec import FrozenModel


class WorkspaceInput(FrozenModel):
    """创建指定运行任务的隔离工作区。"""

    # run_id：字段说明见该模型文档与工作流契约。
    run_id: str
    # task_id：字段说明见该模型文档与工作流契约。
    task_id: str
    # source_project_path：本轮 Git 干净树的业务项目目录。
    source_project_path: str
    # baseline_dir：字段说明见该模型文档与工作流契约。
    baseline_dir: str
    # carrier：字段说明见该模型文档与工作流契约。
    carrier: str
    # framework：字段说明见该模型文档与工作流契约。
    framework: str


class WorkspaceOutput(FrozenModel):
    """隔离工作区路径。"""

    # ai_workspace_path：字段说明见该模型文档与工作流契约。
    ai_workspace_path: str


__all__ = ["WorkspaceInput", "WorkspaceOutput"]
