"""06 确定性门禁。"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from app.infrastructure.filesystem.diff import MAX_FILE_BYTES, MAX_LINE_CHARS, MAX_LINES_PER_FILE, read_text_cached
from app.infrastructure.filesystem.workspace import WorkspaceManager
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


def _render_diff(
    files: list[dict[str, Any]],
    workspace_path: str = "",
    baseline_path: str = "",
) -> str:
    """渲染带上下文的代码差异；完整源码快照超预算时仍能看到修改附近的原文。

    优先从目标 carrier 的工作区和不可变基线生成标准 unified diff（每个修改块带
    3 行上下文），但只把 diff 片段写入 artifact。对超大/非 UTF-8 文件或不可用的
    历史记录，退回门禁已经生成的增删行证据。范围外文件不输出源码内容。
    """
    root = Path(workspace_path).resolve() if workspace_path else None
    baseline = Path(baseline_path).resolve() if baseline_path else None
    parts: list[str] = []
    used_diff_bytes = 0
    omitted_diff_files = 0

    for item in files:
        relative = str(item.get("path") or "").replace("\\", "/").strip()
        relative_path = Path(relative)
        status = str(item.get("status") or "modified").lower()
        if (
            not relative
            or relative_path.is_absolute()
            or ".." in relative_path.parts
            or not relative.lower().endswith(".py")
            or item.get("in_scope") is not True
        ):
            continue
        relative = relative_path.as_posix()

        # Generate contextual hunks from the source snapshot where the text is readable.
        if root is not None:
            current_file = root / relative
            before_file = baseline / relative if baseline is not None else None
            safe_current = not current_file.is_symlink() and _under(root, current_file)
            safe_before = True
            if before_file is not None and baseline is not None:
                safe_before = not before_file.is_symlink() and _under(baseline, before_file)
            if safe_current and safe_before:
                before_exists = bool(before_file and before_file.is_file())
                after_exists = current_file.is_file()
                before_text = read_text_cached(before_file) if before_exists else ""
                after_text = read_text_cached(current_file) if after_exists else ""
                if before_text is not None and after_text is not None:
                    unified = list(difflib.unified_diff(
                        before_text.splitlines(),
                        after_text.splitlines(),
                        fromfile=f"a/{relative}",
                        tofile=f"b/{relative}",
                        n=3,
                        lineterm="",
                    ))
                    if unified:
                        body_lines = unified[2:]
                        truncated_lines = len(body_lines) > MAX_LINES_PER_FILE
                        if truncated_lines:
                            unified = [*unified[:2], *body_lines[:MAX_LINES_PER_FILE]]
                        block: list[str] = []
                        for line in unified:
                            if line.startswith(("--- ", "+++ ")):
                                block.append(line)
                                continue
                            marker = line[:1] if line[:1] in {"+", "-", " ", "@"} else " "
                            body = line[1:] if marker != "@" else line
                            if len(body) > MAX_LINE_CHARS:
                                body = f"{body[:MAX_LINE_CHARS - 1]}…"
                            block.append(body if marker == "@" else f"{marker}{body}")
                        if truncated_lines:
                            block.append(f"# 单文件差异已截断，最多保留 {MAX_LINES_PER_FILE} 行差异内容")
                        block_text = "\n".join(block)
                        block_bytes = len(block_text.encode("utf-8"))
                        if used_diff_bytes + block_bytes <= _REVIEW_DIFF_BUDGET:
                            parts.append(block_text)
                            used_diff_bytes += block_bytes
                        else:
                            omitted_diff_files += 1
                    # A readable current/baseline pair is authoritative even for an empty file diff.
                    continue

        # Fallback for legacy/oversized files where full source text cannot be read.
        lines = list(item.get("lines") or [])
        if not lines:
            continue
        block = [
            f"--- a/{relative}",
            f"+++ b/{relative}",
            f"@@ {status} +{item.get('added', 0)} -{item.get('removed', 0)} @@",
        ]
        for line in lines:
            kind = str(line.get("type") or "")
            marker = {"add": "+", "del": "-", "hunk": ""}.get(kind, " ")
            text = str(line.get("text") or "")
            if len(text) > MAX_LINE_CHARS:
                text = f"{text[:MAX_LINE_CHARS - 1]}…"
            block.append(f"{marker}{text}")
        block_text = "\n".join(block)
        block_bytes = len(block_text.encode("utf-8"))
        if used_diff_bytes + block_bytes <= _REVIEW_DIFF_BUDGET:
            parts.append(block_text)
            used_diff_bytes += block_bytes
        else:
            omitted_diff_files += 1

    if omitted_diff_files:
        parts.append(
            f"# 另有 {omitted_diff_files} 个文件的 Diff 片段超出总容量上限，完整源码可用时仍可在左右对照视图中查看"
        )
    return "\n".join(parts)



# Inline text sources let the workbench render an editor-style, side-by-side diff.
# Keep enough room for a normal multi-file repair and both complete source versions; the
# step record stores only the artifact reference, so this does not inflate steps.json.
_REVIEW_CONTENT_BUDGET = 4 * 1024 * 1024
_REVIEW_CONTENT_FILES = 100
_REVIEW_DIFF_BUDGET = 1 * 1024 * 1024


def _under(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _render_file_contents(
    files: list[dict[str, Any]],
    workspace_path: str,
    baseline_path: str,
    carrier: str,
) -> list[dict[str, Any]]:
    """为审批 UI 保留受限的修改前/修改后文本，不暴露范围外或非 Python 文件。"""
    workspace = Path(workspace_path).resolve()
    baseline = Path(baseline_path).resolve() if baseline_path else None
    try:
        carrier_roots = WorkspaceManager.locate_carrier_roots(workspace, carrier)
    except Exception:
        carrier_roots = []

    payload: list[dict[str, Any]] = []
    used = 0
    for item in files:
        relative = str(item.get("path") or "").replace("\\", "/").strip("/")
        status = str(item.get("status") or "").lower()
        if (
            not relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or not relative.lower().endswith(".py")
            or item.get("in_scope") is not True
            or status not in {"added", "modified", "deleted", "modified_too_large"}
            or not carrier_roots
        ):
            continue

        current_file = workspace / relative
        if not any(_under(root, current_file) for root in carrier_roots):
            continue
        if current_file.is_symlink():
            continue
        before_file = baseline / relative if baseline else None
        if before_file is not None and not _under(baseline, before_file):
            continue
        if before_file is not None and before_file.is_symlink():
            continue

        before_exists = bool(before_file and before_file.is_file())
        after_exists = current_file.is_file()
        before = read_text_cached(before_file) if before_exists else ""
        after = read_text_cached(current_file) if after_exists else ""
        unavailable = (
            (before_exists and before is None)
            or (after_exists and after is None)
        )
        if unavailable:
            payload.append({
                "path": relative,
                "status": status,
                "available": False,
                "reason": f"文件超过 {MAX_FILE_BYTES} 字节或不是 UTF-8 文本，未内嵌完整源码",
                "before": "",
                "after": "",
            })
            continue

        before_text = before or ""
        after_text = after or ""
        size = len(before_text.encode("utf-8")) + len(after_text.encode("utf-8"))
        if len(payload) >= _REVIEW_CONTENT_FILES or used + size > _REVIEW_CONTENT_BUDGET:
            payload.append({
                "path": relative,
                "status": status,
                "available": False,
                "reason": "达到本次差异文本的总容量上限，未内嵌完整源码",
                "before": "",
                "after": "",
            })
            continue
        used += size
        payload.append({
            "path": relative,
            "status": status,
            "available": True,
            "reason": "",
            "before": before_text,
            "after": after_text,
        })
    return payload


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
    # 同时为 UI 提供修改前/修改后源码，有限大小内嵌且只允许目标 carrier Python 文件。
    diff_payload = {
        "format": "side_by_side_v1",
        "diff": _render_diff(all_files, data.ai_workspace_path, data.baseline_dir),
        "files": _render_file_contents(
            all_files,
            data.ai_workspace_path,
            data.baseline_dir,
            data.carrier,
        ),
    }
    ctx.emit_artifact("diff", "artifacts/diff.json", diff_payload)
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
