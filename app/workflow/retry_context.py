"""Capture failed-attempt evidence before resetting files or workflow channels."""

from __future__ import annotations

import difflib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from app.infrastructure.records.store import RecordStore
from app.infrastructure.redaction import redact_output


def _sanitize(value: Any) -> Any:
    """保留原始重试证据，不改变字段值。"""
    from copy import deepcopy
    return deepcopy(value)


def _compact(value: Any, depth: int = 0) -> Any:
    """Bound repeated context before placing it in the next prompt."""
    if isinstance(value, str):
        text = redact_output(value)
        return text[:1000] + ("…（完整内容见重试产物）" if len(text) > 1000 else "")
    if depth >= 4:
        return "详见重试产物"
    if isinstance(value, dict):
        return {str(key): _compact(item, depth + 1) for key, item in list(value.items())[:12]}
    if isinstance(value, list):
        return [_compact(item, depth + 1) for item in value[:8]]
    return value


def capture_retry_context(state: dict[str, Any], store: RecordStore | None = None, *, reuse_existing: bool = True) -> dict[str, Any]:
    """Persist evidence and return a bounded prompt context, independent of SDK memory."""
    attempt = int(state.get("attempt") or 1)
    if store is not None and reuse_existing:
        latest_path = store.task_dir(str(state["run_id"]), str(state["task_id"])) / "retry/latest.json"
        if latest_path.is_file():
            captured = store.read_artifact(str(state["run_id"]), "retry/latest.json", str(state["task_id"]))
            if captured.get("attempt") == attempt and not captured.get("capture_errors"):
                # A replay after reset must not replace the original diff with a clean one.
                return captured
    paths = sorted(set(state.get("changed_files") or (state.get("repair") or {}).get("changed_files") or []))
    payload = {
        "attempt": attempt, "retry_target": str(state.get("retry_target") or ""),
        "failure_reason": str(state.get("retry_reason") or ""), "changed_files": paths,
        **{key: state.get(key) or {} for key in (
            "locate", "repair", "change_plan", "review", "gate_result", "syntax_result", "verify_result", "regression_result",
        )},
    }
    diff = ""
    capture_errors: list[str] = []
    workspace_value, baseline_value = state.get("ai_workspace_path"), state.get("baseline_dir")
    if workspace_value and baseline_value:
        workspace, baseline = Path(workspace_value).resolve(), Path(baseline_value).resolve()
        candidates = set(paths) | set(state.get("baseline_manifest") or {})
        carrier_dir = str((state.get("evidence") or {}).get("carrier_dir") or "")
        if carrier_dir:
            carrier_root = (workspace / carrier_dir).resolve()
            if carrier_root.is_relative_to(workspace) and carrier_root.is_dir():
                candidates.update(path.relative_to(workspace).as_posix() for path in carrier_root.rglob("*")
                                  if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc")
        actual = []
        for relative in sorted(candidates):
            before, after = (baseline / relative).resolve(), (workspace / relative).resolve()
            if not before.is_relative_to(baseline) or not after.is_relative_to(workspace):
                continue
            try:
                if any(path.is_file() and path.stat().st_size > 1_000_000 for path in (before, after)):
                    capture_errors.append(f"{relative}: 文件超过证据大小限制")
                    continue
                old = before.read_text(encoding="utf-8", errors="replace") if before.is_file() else ""
                new = after.read_text(encoding="utf-8", errors="replace") if after.is_file() else ""
            except OSError as error:
                capture_errors.append(f"{relative}: {type(error).__name__}")
                continue
            if old == new:
                continue
            actual.append(relative)
            if len(diff) > 300000:
                continue
            diff += "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                               fromfile=f"baseline/{relative}", tofile=f"attempt-{attempt}/{relative}"))
        if actual:
            payload["declared_changed_files"] = paths
            paths = actual
            payload["changed_files"] = paths
    payload["diff"] = diff
    payload["capture_errors"] = capture_errors
    artifact = f"retry/attempt-{attempt}.json"
    if not reuse_existing:
        artifact = f"retry/attempt-{attempt}-manual-{uuid.uuid4().hex[:12]}.json"
    if store is not None:
        # Keep full evidence locally; the next prompt receives a limited summary.
        store.write_artifact(str(state["run_id"]), artifact, _sanitize(payload), str(state["task_id"]))
    previous = state.get("retry_context") or {}
    history = list(previous.get("history") or [])
    if previous.get("attempt"):
        history.append({key: previous.get(key) for key in ("attempt", "failure_reason", "changed_files")})
    context = {"attempt": attempt, "failure_reason": _compact(payload["failure_reason"]),
               "changed_files": paths[:16], "artifact": artifact, "history": _compact(history[-3:])}
    context["capture_errors"] = capture_errors
    # Reserve space for every outcome before adding detailed patch text.
    for key in ("locate", "repair", "change_plan", "review", "gate_result", "syntax_result", "verify_result", "regression_result"):
        value = _compact(payload[key])
        encoded = json.dumps(value, ensure_ascii=False, default=str)
        context[key] = value if len(encoded) <= 1600 else encoded[:1600] + "…（详见重试产物）"
    context["diff"] = redact_output(diff)[:4000]
    length = len(json.dumps(context, ensure_ascii=False, default=str))
    if length > 12000:
        context["diff"] = context["diff"][:max(0, 4000 - (length - 12000) - 100)]
        context["history"] = _compact(context["history"][-1:])
        for key in ("locate", "repair", "change_plan", "review", "gate_result", "syntax_result", "verify_result", "regression_result"):
            encoded = json.dumps(context[key], ensure_ascii=False, default=str)
            if len(encoded) > 700:
                context[key] = encoded[:600] + "…（详见重试产物）"
        context["context_truncated"] = True
    context = _sanitize(context)
    if store is not None:
        store.write_artifact(str(state["run_id"]), "retry/latest.json", context, str(state["task_id"]))
    return context


def stage_retry_evidence(store: RecordStore, state: dict[str, Any]) -> dict[str, Any]:
    """Expose only this task's archived evidence through a read-only system directory."""
    context = dict(state.get("retry_context") or {})
    artifact = str(context.get("artifact") or "")
    if not artifact:
        return context
    if not re.fullmatch(r"retry/attempt-\d+(?:-manual-[0-9a-f]+)?\.json", artifact):
        raise ValueError("重试产物路径不符合任务证据格式")
    from app.agents.policy import retry_evidence_dir

    run_id, task_id = str(state["run_id"]), str(state["task_id"])
    workspace = Path(str(state["ai_workspace_path"])).resolve()
    directory = retry_evidence_dir(workspace, run_id, task_id)
    if not directory.resolve().is_relative_to(workspace):
        raise ValueError("重试只读证据目录越出当前任务工作区")
    payload = store.read_artifact(run_id, artifact, task_id)
    directory.mkdir(parents=True, exist_ok=True)
    for name, value in (("owner.json", {"run_id": run_id, "task_id": task_id}), (Path(artifact).name, payload)):
        target = directory / name
        if target.is_symlink():
            raise ValueError("重试证据文件不能是符号链接")
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
    context["artifact_path"] = str((directory / Path(artifact).name).resolve())
    return context
