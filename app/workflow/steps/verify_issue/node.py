"""07 独立验证。"""

from __future__ import annotations


from app.domain.errors import StepFailed
from app.application.context import StepContext
from app.infrastructure.redaction import redact_output
from app.workflow.steps.verify_issue.models import VerifyInput, VerifyOutput


def run(ctx: StepContext, data: VerifyInput) -> VerifyOutput:
    """未配置验证器就失败，不跳过。"""
    callback = ctx.services.metadata.get("independent_verify")
    if not callable(callback):
        raise StepFailed("独立验证器未配置", {"passed": False, "reason": "独立验证器未配置"})
    # 共享树是给模型阅读的脱敏快照，会删除 .zip 等非源码资产；重放必须在
    # 未脱敏的干净 Git 业务目录上执行，否则 framework 资源会缺文件。
    clean_source_path = ""
    if data.source_repo_path and ctx.services.workspaces is not None:
        try:
            clean_source_path = str(ctx.services.workspaces.project_root(data.source_repo_path))
        except Exception:
            clean_source_path = ""
    verified = callback(
        {
            "task_id": data.task_id,
            "carrier": data.carrier,
            "problem_key": data.problem_key,
            "ai_workspace_path": data.ai_workspace_path,
            "changed_files": list(data.changed_files),
            "source_project_path": data.source_project_path,
            "clean_source_path": clean_source_path,
            "normalized_log": dict(data.normalized_log),
        }
    )
    if not isinstance(verified, dict):
        reason = (
            redact_output(" ".join(str(verified.get("reason") or "独立验证失败").split()))[:2000]
            if isinstance(verified, dict)
            else "独立验证返回无效"
        )
        raise StepFailed(reason, {"passed": False, "reason": reason, "mode": "unavailable"})
    if verified.get("status") == "unverified":
        return VerifyOutput(
            passed=False,
            status="unverified",
            reason=str(verified.get("reason") or "业务问题未验证"),
            mode=str(verified.get("mode") or "unverified"),
        )
    if verified.get("passed") is not True:
        reason = redact_output(" ".join(str(verified.get("reason") or "独立验证失败").split()))[:2000]
        raise StepFailed(reason, {"passed": False, "reason": reason, "mode": str(verified.get("mode") or "unavailable")})
    return VerifyOutput(
        passed=True,
        status=str(verified.get("status") or "verified"),
        reason=str(verified.get("reason") or ""),
        mode=str(verified.get("mode") or "custom"),
    )


