"""06 确定性门禁。"""

from __future__ import annotations

from typing import Any

from app.infrastructure.filesystem.diff import MAX_LINE_CHARS
from app.domain.errors import StepFailed
from app.application.context import StepContext
from app.workflow.steps.check_changes.models import CheckInput, CheckOutput, FileEvidence
from app.workflow.steps.check_changes.verification import validate_changed_files
from config.settings import settings


MAX_OUTPUT_ITEMS = settings.check_changes_max_output_items
_TRUNCATED_REASON = "…其余 {count} 条已省略"


def _limit_items(items: list[Any], *, kind: str) -> list[Any]:
    """限制步骤记录中的列表体量，保留门禁结论并避免记录存储超限。"""
    if len(items) <= MAX_OUTPUT_ITEMS:
        return items
    omitted = len(items) - MAX_OUTPUT_ITEMS
    if kind == "files":
        placeholder: Any = {
            "path": "",
            "in_scope": True,
            "exists": True,
            "status": "truncated",
            "problems": [_TRUNCATED_REASON.format(count=omitted)],
        }
    else:
        placeholder = {
            "file": "",
            "reason": _TRUNCATED_REASON.format(count=omitted),
            "code": "TRUNCATED",
        }
    return [*items[:MAX_OUTPUT_ITEMS], placeholder]


def _render_diff(files: list[dict[str, Any]]) -> str:
    """把逐文件差异行拼成可直接阅读的 unified diff 文本。

    ``validate_changed_files`` 的每文件结论里只留了增删行数，没有行内容；这里从
    工作区与基线副本重新取一次（``file_changes`` 带缓存），把"改了哪几行"完整落盘。
    这里不再截断：审查要看的就是完整差异，记录里只留逐文件摘要与产物引用。
    """
    parts: list[str] = []
    for item in files:
        lines = list(item.get("lines") or [])
        if not lines:
            continue
        block = [
            f"--- a/{item['path']}\n"
            f"+++ b/{item['path']}\n"
            f"@@ {item.get('status')} +{item.get('added', 0)} -{item.get('removed', 0)} @@"
        ]
        for line in lines:
            marker = {"add": "+", "del": "-", "hunk": "@@"}.get(str(line.get("type")), " ")
            text = str(line.get("text") or "")
            if len(text) > MAX_LINE_CHARS:
                text = f"{text[:MAX_LINE_CHARS]}…"
            block.append(f"{marker}{text}")
        parts.append("\n".join(block))
    return "\n".join(parts)


def _evidence(raw: dict[str, Any]) -> FileEvidence:
    """把门禁的逐文件结论转成落盘契约。"""
    return FileEvidence(
        path=str(raw.get("path") or ""),
        in_scope=bool(raw.get("in_scope", True)),
        exists=bool(raw.get("exists", True)),
        status=str(raw.get("status") or "unchanged"),
        added=int(raw.get("added") or 0),
        removed=int(raw.get("removed") or 0),
        size=int(raw.get("size") or 0),
        sha256=str(raw.get("sha256") or ""),
        nlink=int(raw.get("nlink") or 0),
        secret_hits=int(raw.get("secret_hits") or 0),
        secret_samples=list(raw.get("secret_samples") or []),
        ast=str(raw.get("ast") or "skipped"),
        dangerous_calls=list(raw.get("dangerous_calls") or []),
        problems=list(raw.get("problems") or []),
    )


def run(ctx: StepContext, data: CheckInput) -> CheckOutput:
    """执行门禁，把完整代码差异落产物，逐文件证据落步骤记录。"""
    if not data.ai_workspace_path:
        raise StepFailed("缺少确定性检查工作区")
    result = validate_changed_files(
        data.ai_workspace_path,
        data.carrier,
        data.changed_files,
        data.baseline_manifest or None,
        data.baseline_dir or None,
    )
    all_files = list(result.get("files") or [])
    files = _limit_items(all_files, kind="files")
    failures = _limit_items(list(result.get("failures") or []), kind="failures")
    # 判定失败也要留下完整差异：下一轮修复看的就是这份。
    ctx.emit_artifact("diff", "artifacts/diff.json", {"diff": _render_diff(all_files)})
    output = CheckOutput(
        passed=result.get("passed") is True,
        reason=str(result.get("reason") or ""),
        failure_count=len(result.get("failures") or []),
        changed_files=list(data.changed_files),
        checks=list(result.get("checks") or []),
        scope=dict(result.get("scope") or {}),
        claim=dict(result.get("claim") or {}),
        totals=dict(result.get("totals") or {}),
        files=[_evidence(item) for item in files],
        failures=failures,
    )
    if not output.passed:
        raise StepFailed(output.reason or "确定性检查失败", output.model_dump())
    return output
