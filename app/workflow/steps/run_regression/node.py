"""08 准生产回归。"""

from __future__ import annotations

from typing import Any

from app.domain.errors import StepFailed
from app.application.context import StepContext
from app.infrastructure.redaction import redact_output
from app.workflow.steps.run_regression.models import RegressionInput, RegressionOutput
from app.workflow.steps.run_regression.regression import RegressionRunner

# 每条命令留存脱敏后输出的尾部。定位失败原因看的是报错现场，尾部最有信息量。
_TAIL_CHARS = 8000


def _entry(raw: dict[str, Any]) -> dict[str, Any]:
    """把一条命令的结果脱敏并留存。

    顺序固定：取原始输出 → 记原始字符数 → 脱敏 → 截取脱敏后文本的尾部。先截断会
    砍掉识别凭据所需的上下文，尾部令牌就会漏网。
    """
    stdout = str(raw.get("stdout") or "")
    stderr = str(raw.get("stderr") or "")
    masked_stdout = redact_output(stdout)
    masked_stderr = redact_output(stderr)
    return {
        "command": [redact_output(str(part)) for part in raw.get("command") or []],
        "returncode": int(raw.get("returncode") or 0),
        # 原始字符数：脱敏会改变长度，两者不相等，必须无歧义。
        "stdout_chars": len(stdout),
        "stderr_chars": len(stderr),
        "stdout": masked_stdout[-_TAIL_CHARS:],
        "stderr": masked_stderr[-_TAIL_CHARS:],
        "truncated": len(masked_stdout) > _TAIL_CHARS or len(masked_stderr) > _TAIL_CHARS,
    }


def run(ctx: StepContext, data: RegressionInput) -> RegressionOutput:
    """在从源仓库克隆的独立 Git 工作区里跑配置里的固定命令。"""
    if ctx.services.workspaces is None:
        raise StepFailed("隔离工作区未配置，无法执行准生产回归")
    if not ctx.services.source_repo:
        raise StepFailed("源仓库未配置，无法建立独立回归环境")
    result = RegressionRunner(
        ctx.services.workspaces,
        ctx.services.verify_commands,
        ctx.services.source_repo,
    ).run(
        {
            "ai_workspace_path": data.ai_workspace_path,
            "carrier": data.carrier,
            "changed_files": list(data.changed_files),
            "source_repo_path": str(ctx.services.source_repo),
            "task_id": data.task_id,
        }
    )
    regression = result.get("regression")
    results = regression.get("results") if isinstance(regression, dict) else None
    results = results if isinstance(results, list) else []
    reason = str(result.get("reason") or "")
    count = len(results)
    # 没跑成命令（环境建立失败、未配置命令）时没有报告可留，只报原因。
    if results:
        ctx.emit_artifact(
            "regression_report",
            "artifacts/regression_report.json",
            {"reason": reason, "commands": [_entry(item) for item in results]},
        )
    output = RegressionOutput(passed=result.get("passed") is True, reason=reason, command_count=count)
    if not output.passed:
        raise StepFailed(output.reason or "准生产回归失败", output.model_dump())
    return output


__all__ = ["run"]