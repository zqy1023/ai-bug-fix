"""08 准生产回归。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from core.contracts import StepContext
from core.errors import StepFailed
from agents.review.gates.run_regression.models import RegressionInput, RegressionOutput
from agents.review.gates.run_regression.regression import RegressionRunner
from config.settings import settings

# 步骤摘要留存输出尾部，完整输出另存报告。
_TAIL_CHARS = 8000


def _entry(raw: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    """保留原值；摘要截取尾部并标记，完整归档不裁剪。"""
    stdout = str(raw.get("stdout") or "")
    stderr = str(raw.get("stderr") or "")
    masked_stdout = stdout
    masked_stderr = stderr
    return {
        "command": [str(part) for part in raw.get("command") or []],
        "returncode": raw.get("returncode"),
        # 字符数始终对应完整原文。
        "stdout_chars": len(stdout),
        "stderr_chars": len(stderr),
        "stdout": masked_stdout if full else masked_stdout[-_TAIL_CHARS:],
        "stderr": masked_stderr if full else masked_stderr[-_TAIL_CHARS:],
        "truncated": not full and (len(masked_stdout) > _TAIL_CHARS or len(masked_stderr) > _TAIL_CHARS),
    }


def _classify_regression_result(
    result: Mapping[str, Any],
    request_result: Mapping[str, Any],
    supplemental: Mapping[str, Any],
) -> tuple[bool, str, str, str]:
    """把回归执行结论收敛成 ``(passed, status, reason, failure_kind)``。

    模块级纯函数：只读输入、不触碰状态，原来散在 ``run`` 里的三重三元逻辑
    逐分支等价迁移到这里。``supplemental`` 目前不参与结论分类（补充验证只落
    报告与输出字段），保留在签名里让本函数成为结论的唯一出口。

    ``unverified`` 有两处口径，语义刻意不同，顺序即优先级：

    1. ``request_result.status == "unverified"``：真实请求已发出但未完成业务
       验证——结论是"已执行、业务结果未确认"，原因取请求侧原因；
    2. ``failure_kind == "execution"`` 或存在 ``workspace_changes``：回归环境
       执行失败或命令违反了"只测试"约束——同样置为 unverified，但必须带
       ``policy``/``execution`` 失败类别；该口径覆盖口径一（写在后面）。
    """
    regression = result.get("regression") if isinstance(result.get("regression"), dict) else {}
    raw_results = regression.get("results") if isinstance(regression, dict) else None
    results = raw_results if isinstance(raw_results, list) else []
    workspace_changes = regression.get("workspace_changes") if isinstance(regression, dict) else None
    syntax_only = bool(results) and all(
        "-m" in (item.get("command") or []) and "py_compile" in (item.get("command") or [])
        for item in results
    )
    passed = result.get("passed") is True and bool(results) and (not request_result or request_result.get("passed") is True)
    status = ("syntax_only" if syntax_only else "verified" if results else "not_executed") if result.get("passed") is True else "failed"
    if result.get("passed"):
        reason = (
            str(request_result.get("reason")) if request_result else
            "仅语法编译通过；未执行行为或集成回归" if syntax_only else
            "固定回归命令执行通过，具体覆盖范围由审查核对" if results else
            "没有适用于本轮变更的固定命令，未执行补充回归"
        )
    else:
        reason = str(result.get("reason") or "")
    # 口径一：请求侧业务验证未完成。
    if result.get("passed") is True and request_result.get("status") == "unverified":
        status = "unverified"
        reason = str(request_result.get("reason") or "真实请求未完成业务验证")
    # 口径二：执行失败或工作区被改写，覆盖口径一并补失败类别。
    failure_kind = ""
    if result.get("failure_kind") == "execution" or workspace_changes:
        passed = False
        status = "unverified"
        failure_kind = "policy" if workspace_changes else "execution"
    return passed, status, reason, failure_kind


def run(ctx: StepContext, data: RegressionInput) -> RegressionOutput:
    """在从源仓库克隆的独立 Git 工作区里跑配置里的固定命令。"""
    from workflow.engine.evidence_version import evidence_version
    version = evidence_version(data.ai_workspace_path, data.changed_files)
    from workflow.engine.validation import fingerprint
    input_id = fingerprint(data.normalized_log)
    previous = data.previous_result
    if (previous.get("input_id") == input_id and previous.get("passed") is True
        and previous.get("status") == "verified" and
        (previous.get("evidence_version") or {}).get("patch_id") == version["patch_id"]):
        return RegressionOutput.model_validate(previous).model_copy(update={"reused": True})
    request_executor = ctx.services.metadata.get("request_regression")
    if not ctx.services.verify_commands and not callable(request_executor):
        return RegressionOutput(
            passed=False, status="not_configured", evidence_version=version,
            reason="未配置真实请求执行器和补充命令，未取得 server_dev.main 的业务结果",
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
        }
    )
    regression = result.get("regression")
    results = regression.get("results") if isinstance(regression, dict) else None
    results = results if isinstance(results, list) else []
    request_result = regression.get("request_regression", {}) if isinstance(regression, dict) else {}
    supplemental: dict[str, Any] = {}
    callback = ctx.services.metadata.get("independent_verify")
    if callable(callback) and data.business_verification_status == "unverified" and data.baseline_dir:
        from agents.review.gates.run_regression.supplemental import verify_captured_response
        from infrastructure.storage.redaction import redact_value
        clean_source = str(ctx.services.workspaces.project_root(ctx.services.source_repo))
        try:
            supplemental = verify_captured_response(request_result, data.normalized_log, {
                "task_id": data.task_id, "carrier": data.carrier, "ai_workspace_path": data.ai_workspace_path,
                "changed_files": list(data.changed_files), "baseline_dir": data.baseline_dir,
                "source_project_path": data.source_project_path, "clean_source_path": clean_source,
            }, callback)
        except (OSError, ValueError, TypeError) as error:
            from infrastructure.storage.redaction import public_exception
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
    request_result = json.loads(json.dumps(request_result, ensure_ascii=False))
    workspace_changes = (
        regression.get("workspace_changes") if isinstance(regression, dict) else None
    )
    # 报告里的 reason 始终是执行器的原始原因；输出结论的分类见 _classify_regression_result。
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
            "runtime_artifacts": regression.get("runtime_artifacts", []) if isinstance(regression, dict) else [],
        }
        if workspace_changes:
            report["workspace_changes"] = workspace_changes
        ctx.emit_artifact(
            "regression_report",
            "artifacts/regression_report.json",
            report,
        )
    passed, status, reason, failure_kind = _classify_regression_result(result, request_result, supplemental)
    output = RegressionOutput(
        input_id=input_id,
        passed=passed,
        evidence_version=version,
        status=status,
        mode="syntax" if syntax_only else "regression",
        reason=reason,
        command_count=count,
        commands=[_entry(item) for item in results],
        report_path="artifacts/regression_report.json" if results or workspace_changes or request_result else "",
        request_regression={key: value for key, value in request_result.items() if key not in {"commands", "script", "response", "result", "effective_requests"}},
        supplemental_verification=supplemental,
        failure_kind=failure_kind,
    )
    if evidence_version(data.ai_workspace_path, data.changed_files) != version:
        raise StepFailed("回归期间源码发生变化，结果已失效，请重新执行检查")
    if output.status == "failed":
        raise StepFailed(output.reason or "准生产回归失败", output.model_dump())
    return output


__all__ = ["run"]
