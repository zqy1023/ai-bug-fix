"""07 独立验证。"""

from __future__ import annotations

from app.application.context import StepContext
from app.domain.errors import StepFailed
from app.infrastructure.redaction import redact_output, redact_value
from app.workflow.steps.verify_issue.assessment import verification_is_complete
from app.workflow.steps.verify_issue.models import VerifyInput, VerifyOutput


def run(ctx: StepContext, data: VerifyInput) -> VerifyOutput:
    """未配置验证器就失败，不跳过。"""
    from app.workflow.evidence_version import evidence_version
    version = evidence_version(data.ai_workspace_path, data.changed_files, data.baseline_dir)
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
            "baseline_dir": data.baseline_dir,
            "normalized_log": dict(data.normalized_log),
            "validation_requirements": data.validation_requirements,
        }
    )
    if not isinstance(verified, dict):
        reason = "独立验证返回无效"
        raise StepFailed(reason, {"passed": False, "reason": reason, "mode": "unavailable"})
    if evidence_version(data.ai_workspace_path, data.changed_files, data.baseline_dir) != version:
        raise StepFailed("验证期间源码发生变化，验证结果已失效，请重新执行检查")
    # 旧回调的 passed=True/syntax_only 不能再被默认状态提升为业务通过。
    if verified.get("passed") is True and not verification_is_complete(verified):
        verified = {**verified, "passed": False, "status": "unverified",
                    "reason": "验证器未提供当前契约下的业务断言证据；语法或旧版回放不能视为业务通过"}
    if verified.get("status") not in {"verified", "failed", "unverified"}:
        verified = {**verified, "passed": False, "status": "unverified",
                    "reason": str(verified.get("reason") or "验证器未声明可核验的业务结论")}
    from app.workflow.validation_requirements import assess
    requirements = assess(data.validation_requirements, verified, version)
    safe_report = {**redact_value(verified), "evidence_version": version, "validation_requirements": requirements}
    ctx.emit_artifact("verification_report", "artifacts/verification_report.json", safe_report)
    replay = {key: value for key, value in safe_report.items() if key.startswith("replay_")}
    details = {"replay": replay, "report_path": "artifacts/verification_report.json", "evidence_version": version,
               "verification": safe_report.get("verification") or {}, "validation_requirements": requirements}
    if verified.get("status") == "unverified":
        return VerifyOutput(
            passed=False,
            status="unverified",
            reason=str(verified.get("reason") or "业务问题未验证"),
            mode=str(verified.get("mode") or "unverified"),
            **details,
        )
    if verified.get("passed") is not True:
        reason = redact_output(" ".join(str(verified.get("reason") or "独立验证失败").split()))[:2000]
        raise StepFailed(reason, {"passed": False, "status": "failed", "reason": reason, "mode": str(verified.get("mode") or "unavailable"), **details})
    return VerifyOutput(
        passed=True,
        status=str(verified.get("status") or "verified"),
        reason=str(verified.get("reason") or ""),
        mode=str(verified.get("mode") or "custom"),
        **details,
    )


