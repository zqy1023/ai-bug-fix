"""创建单任务 AI 隔离工作区。"""

from __future__ import annotations


from core.errors import StepFailed
from core.contracts import StepContext
from .models import WorkspaceInput, WorkspaceOutput
from infrastructure.storage.redaction import public_exception


def run(ctx: StepContext, data: WorkspaceInput) -> WorkspaceOutput:
    """每个任务只复制自己的 carrier，其余目录共享本轮只读源码树。"""
    if ctx.services.workspaces is None or not data.source_project_path:
        raise StepFailed("共享源码树未准备，无法创建任务隔离区")
    try:
        task_key = f"{data.run_id}-{data.task_id}"
        workspace = str(
            ctx.services.workspaces.create_task_workspace(
                data.source_project_path,
                task_key,
                baseline_dir=data.baseline_dir,
                carrier=data.carrier,
                framework=data.framework,
            )
        )
    except Exception as error:
        raise StepFailed(public_exception(error, prefix="任务隔离区创建失败")) from error
    return WorkspaceOutput(ai_workspace_path=workspace)



