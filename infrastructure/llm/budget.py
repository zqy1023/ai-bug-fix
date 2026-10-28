"""给最终任务上下文设预算；完整证据留在本任务只读文件中。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from infrastructure.llm.models import PromptContext


def evidence_directory(workspace: Path, run_id: str, task_id: str, role: str) -> Path:
    identity = hashlib.sha256(f"{run_id}\0{task_id}\0{role}".encode()).hexdigest()
    return workspace / ".evidence" / "context" / identity


def readable_context(workspace: Path, run_id: str, task_id: str, role: str) -> list[Path]:
    directory = evidence_directory(workspace, run_id, task_id, role)
    if not directory.is_dir() or any(p.is_symlink() for p in (directory, directory.parent, directory.parent.parent, directory / "owner.json")):
        return []
    try:
        owner = json.loads((directory / "owner.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [directory.resolve()] if owner == {"run_id": run_id, "task_id": task_id, "role": role} else []


def bounded_context(context: PromptContext, workspace: str) -> PromptContext:
    """按完整序列化长度计量，引用大证据而不截断其唯一副本。"""
    limit = context.budget.get("max_context_chars", 50000)
    value = context.model_dump(mode="json")
    directory = evidence_directory(Path(workspace).resolve(), context.run_id, context.task_id, context.agent_role)
    if any(p.is_symlink() for p in (directory, directory.parent, directory.parent.parent, directory / "owner.json")):
        raise ValueError("任务上下文证据目录不能包含符号链接")
    if directory.exists() and not readable_context(Path(workspace).resolve(), context.run_id, context.task_id, context.agent_role):
        raise ValueError("任务上下文证据目录所有者或路径不合法")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "owner.json").write_text(json.dumps({"run_id": context.run_id, "task_id": context.task_id,
                                                    "role": context.agent_role}), encoding="utf-8")
    original = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    target = directory / f"attempt-{context.attempt}.json"
    if target.is_symlink():
        raise ValueError("上下文证据文件不能是符号链接")
    target.write_text(original, encoding="utf-8")
    value["allowed_read_roots"] = list(dict.fromkeys([*value["allowed_read_roots"], str(directory.resolve())]))
    value["evidence"]["full_context"] = {"path": str(target), "sha256": hashlib.sha256(original.encode()).hexdigest(),
                                         "notice": "引用字段的完整值在此文件；下结论前必须读取涉及异常与分支的完整证据"}
    # 重复的摘要不能比唯一的实际执行断言占用更多上下文。
    value["evidence"].pop("deterministic_analysis", None)
    value["evidence"].pop("deterministic_context", None)
    snapshot = value["source_snapshot"]
    manifest = snapshot.pop("baseline_manifest", None)
    if isinstance(manifest, dict):
        snapshot["baseline_file_count"] = len(manifest)
    for key in ("verification_results", "regression_results"):
        report = value[key]
        bulky = {"replay", "commands", "script", "response", "stdout", "stderr"}
        removed = [name for name in report if name in bulky]
        for name in removed:
            report.pop(name)
        if removed:
            report["full_evidence"] = {"path": str(target), "json_path": key, "fields": removed}

    def size() -> int:
        # Renderer omits agent_role and adds a small Markdown wrapper.
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"))) + 40

    for threshold in (4000, 1600, 600, 200):
        if size() <= limit:
            break
        def compact(item: Any, pointer: str) -> Any:
            if isinstance(item, str) and len(item) > threshold:
                return {"evidence_path": str(target), "json_path": pointer,
                        "chars": len(item), "sha256": hashlib.sha256(item.encode()).hexdigest()}
            if isinstance(item, list):
                return [compact(v, f"{pointer}[{i}]") for i, v in enumerate(item)]
            if isinstance(item, dict):
                if "evidence_path" in item or "full_evidence" == pointer.rsplit(".", 1)[-1] or "full_context" == pointer.rsplit(".", 1)[-1]:
                    return item
                return {k: compact(v, f"{pointer}.{k}") for k, v in item.items()}
            return item
        for key in ("problem", "evidence", "gate_results", "verification_results", "regression_results"):
            # Top-level role/identity/permissions and required protocol fields stay exact.
            value[key] = compact(value[key], key)
        if len(value["diff_summary"]) > threshold:
            value["diff_summary"] = f"完整差异见 {target} 的 diff_summary；必须核对全部变更"
    # 大量短字段/逐项收据也可能超预算；缩短长字符串无法解决结构体积。
    # 整份结构仍在上述带哈希的只读文件中，保留状态摘要和精确 JSON 路径。
    for key in sorted(("verification_results", "regression_results", "gate_results", "evidence"),
                      key=lambda k: len(json.dumps(value[k], ensure_ascii=False)), reverse=True):
        if size() <= limit:
            break
        report = value[key]
        summary = {k: report[k] for k in ("passed", "status", "mode", "evidence_version") if k in report}
        if key == "evidence" and "full_context" in report:
            summary["full_context"] = report["full_context"]
        value[key] = {**summary, "full_evidence": {"path": str(target), "json_path": key,
                                                   "notice": "完整结构及逐项执行收据须读取该文件后核对"}}
    if size() > limit:
        raise ValueError(f"任务上下文仍需 {size()} 字符，超过预算 {limit}；未调用模型，完整输入已留存")
    return PromptContext.model_validate(value)
