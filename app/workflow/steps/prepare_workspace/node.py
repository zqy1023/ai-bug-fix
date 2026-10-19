"""创建单任务 AI 隔离工作区。"""

from __future__ import annotations


from app.domain.errors import StepFailed
from app.application.context import StepContext
from app.workflow.steps.prepare_workspace.models import WorkspaceInput, WorkspaceOutput
from app.infrastructure.redaction import public_exception


def run(ctx: StepContext, data: WorkspaceInput) -> WorkspaceOutput:
    """每个任务拥有独立源码树，目标 carrier 从不可变快照恢复。"""
    if ctx.services.workspaces is None or not data.source_repo_path:
        raise StepFailed("源仓库未配置，无法创建任务隔离区")
    try:
        task_key = f"{data.run_id}-{data.task_id}"
        workspace = str(
            ctx.services.workspaces.create_task_workspace(
                data.source_repo_path,
                task_key,
                source_project_path=data.source_project_path,
                baseline_dir=data.baseline_dir,
                carrier=data.carrier,
                framework=data.framework,
            )
        )
    except Exception as error:
        raise StepFailed(public_exception(error, prefix="任务隔离区创建失败")) from error
    return WorkspaceOutput(ai_workspace_path=workspace)


