"""08 准生产回归。"""

from __future__ import annotations

import json
from typing import Any

from app.application.context import StepContext
from app.domain.errors import StepFailed
from app.infrastructure.redaction import redact_output
from app.workflow.steps.run_regression.models import RegressionInput, RegressionOutput
from app.workflow.steps.run_regression.regression import RegressionRunner
from config.settings import settings

# 步骤摘要留存输出尾部，完整输出另存报告。
_TAIL_CHARS = settings.regression_tail_chars


def _entry(raw: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    """保留原值；摘要截取尾部并标记，完整归档不裁剪。"""
    stdout = str(raw.get("stdout") or "")
    stderr = str(raw.get("stderr") or "")
    masked_stdout = redact_output(stdout)
    masked_stderr = redact_output(stderr)
    return {
        "command": [redact_output(str(part)) for part in raw.get("command") or []],
        "returncode": raw.get("returncode"),
        # 字符数始终对应完整原文。
        "stdout_chars": len(stdout),
        "stderr_chars": len(stderr),
        "stdout": masked_stdout if full else masked_stdout[-_TAIL_CHARS:],
        "stderr": masked_stderr if full else masked_stderr[-_TAIL_CHARS:],
        "truncated": not full and (len(masked_stdout) > _TAIL_CHARS or len(masked_stderr) > _TAIL_CHARS),
    }


def run(ctx: StepContext, data: RegressionInput) -> RegressionOutput:
    """在从源仓库克隆的独立 Git 工作区里跑配置里的固定命令。"""
    from app.workflow.evidence_version import evidence_version
    version = evidence_version(data.ai_workspace_path, data.changed_files)
    request_executor = ctx.services.metadata.get("request_regression")
    if not ctx.services.verify_commands and not callable(request_executor):
        return RegressionOutput(
            passed=False, status="not_configured", evidence_version=version,
            validation_requirements=data.validation_requirements,
            reason="未配置固定补充回归命令；没有执行行为回归，覆盖是否足够由审查对照实际变更判断",
        )
    if ctx.services.workspaces is None:
        raise StepFailed("隔离工作区未配置，无法执行准生产回归")
    if not ctx.services.source_repo:
        raise StepFailed("源仓库未配置，无法建立独立回归环境")
    result = RegressionRunner(
        ctx.services.workspaces,
        ctx.services.verify_commands,
        ctx.services.source_repo,
        timeout_seconds=ctx.services.command_timeout_seconds,
        request_executor=request_executor if callable(request_executor) else None,
    ).run(
        {
            "ai_workspace_path": data.ai_workspace_path,
            "carrier": data.carrier,
            "changed_files": list(data.changed_files),
            "source_repo_path": str(ctx.services.source_repo),
            "task_id": data.task_id,
            "normalized_log": data.normalized_log,
            "validation_requirements": data.validation_requirements,
        }
    )
    regression = result.get("regression")
    results = regression.get("results") if isinstance(regression, dict) else None
    results = results if isinstance(results, list) else []
    request_result = regression.get("request_regression", {}) if isinstance(regression, dict) else {}
    supplemental: dict[str, Any] = {}
    callback = ctx.services.metadata.get("independent_verify")
    if callable(callback) and data.business_verification_status == "unverified" and data.baseline_dir:
        from app.workflow.steps.run_regression.supplemental import verify_captured_response
        from app.infrastructure.redaction import redact_value
        clean_source = str(ctx.services.workspaces.project_root(ctx.services.source_repo))
        try:
            supplemental = verify_captured_response(request_result, data.normalized_log, {
                "task_id": data.task_id, "carrier": data.carrier, "ai_workspace_path": data.ai_workspace_path,
                "changed_files": list(data.changed_files), "baseline_dir": data.baseline_dir,
                "source_project_path": data.source_project_path, "clean_source_path": clean_source,
            }, callback)
        except (OSError, ValueError, TypeError) as error:
            from app.infrastructure.redaction import public_exception
            supplemental = {"passed": False, "status": "unverified", "reason": public_exception(error, prefix="新正文补充验证未完成")}
        if supplemental:
            supplemental = {**redact_value(supplemental), "evidence_version": version}
            path = "artifacts/supplemental_verification_report.json"
            ctx.emit_artifact("supplemental_verification_report", path, supplemental)
            supplemental = {key: supplemental[key] for key in
                            ("passed", "status", "reason", "mode", "verification", "sample_source", "evidence_version")
                            if key in supplemental}
            supplemental["report_path"] = path
    # 请求、脚本、原始响应与命令输出原样写入完整报告。
    request_result = json.loads(redact_output(json.dumps(request_result, ensure_ascii=False)))
    workspace_changes = (
        regression.get("workspace_changes") if isinstance(regression, dict) else None
    )
    from app.workflow.validation_requirements import assess
    requirements = assess(data.validation_requirements, request_result, version)
    # 已完成的问题验证收据不因请求回归缺少该收据而退回未验证。
    prior = {str(x.get("id")): x for x in data.validation_requirements}
    requirements = [prior[str(x["id"])] if x["status"] == "unverified" and
                    prior.get(str(x["id"]), {}).get("status") in {"passed", "failed"} and
                    (prior[str(x["id"])].get("evidence_version") or {}).get("patch_id") == version["patch_id"] else x
                    for x in requirements]
    reason = str(result.get("reason") or "")
    count = len(results)
    syntax_only = bool(results) and all(
        "-m" in (item.get("command") or []) and "py_compile" in (item.get("command") or [])
        for item in results
    )
    # 没跑成命令（环境建立失败、未配置命令）时没有报告可留，只报原因。
    if results or workspace_changes or request_result:
        report: dict[str, Any] = {
            "reason": reason,
            "commands": [_entry(item, full=True) for item in results],
            "request_regression": request_result,
            "supplemental_verification": supplemental,
            "validation_requirements": requirements,
            "runtime_artifacts": regression.get("runtime_artifacts", []) if isinstance(regression, dict) else [],
        }
        if workspace_changes:
            report["workspace_changes"] = workspace_changes
        ctx.emit_artifact(
            "regression_report",
            "artifacts/regression_report.json",
            report,
        )
    output = RegressionOutput(
        passed=result.get("passed") is True and bool(results) and (not request_result or request_result.get("passed") is True),
        evidence_version=version,
        status=("syntax_only" if syntax_only else "verified" if results else "not_executed") if result.get("passed") is True else "failed",
        mode="syntax" if syntax_only else "regression",
        reason=(str(request_result.get("reason")) if request_result else "仅语法编译通过；未执行行为或集成回归" if syntax_only else
                "固定回归命令执行通过，具体覆盖范围由审查核对" if results else "没有适用于本轮变更的固定命令，未执行补充回归")
        if result.get("passed") else reason,
        command_count=count,
        commands=[_entry(item) for item in results],
        report_path="artifacts/regression_report.json" if results or workspace_changes or request_result else "",
        request_regression={key: value for key, value in request_result.items() if key not in {"commands", "script", "response", "result", "effective_requests"}},
        supplemental_verification=supplemental,
        validation_requirements=requirements,
    )
    if result.get("passed") is True and request_result.get("status") == "unverified":
        output = output.model_copy(update={"status": "unverified", "reason": str(request_result.get("reason") or "真实请求未完成业务验证")})
    if evidence_version(data.ai_workspace_path, data.changed_files) != version:
        raise StepFailed("回归期间源码发生变化，结果已失效，请重新执行检查")
    if output.status == "failed":
        raise StepFailed(output.reason or "准生产回归失败", output.model_dump())
    return output


__all__ = ["run"]
