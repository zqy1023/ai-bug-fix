"""07 独立验证。"""

from __future__ import annotations


from app.domain.errors import StepFailed
from app.application.context import StepContext
from app.workflow.steps.verify_issue.models import VerifyInput, VerifyOutput


def run(ctx: StepContext, data: VerifyInput) -> VerifyOutput:
    """未配置验证器就失败，不跳过。"""
    callback = ctx.services.metadata.get("independent_verify")
    if not callable(callback):
        raise StepFailed("独立验证器未配置", {"passed": False, "reason": "独立验证器未配置"})
    verified = callback(
        {
            "task_id": data.task_id,
            "carrier": data.carrier,
            "problem_key": data.problem_key,
            "ai_workspace_path": data.ai_workspace_path,
            "changed_files": list(data.changed_files),
        }
    )
    if not isinstance(verified, dict) or verified.get("passed") is not True:
        reason = str(verified.get("reason") or "独立验证失败") if isinstance(verified, dict) else "独立验证返回无效"
        raise StepFailed(reason, {"passed": False, "reason": reason})
    return VerifyOutput(passed=True, reason=str(verified.get("reason") or ""))


