"""状态快照与阶段数据移交 (Handoff)。

作为主图与任务子图状态快照、步骤结果落盘、任务独立上下文与重试证据捕获的单一写入边界。
"""

from __future__ import annotations

import difflib
import hashlib
import json
import operator
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Annotated, Any, TYPE_CHECKING

from langgraph.errors import GraphInterrupt
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import TypedDict

from core.contracts import (
    Assessment,
    ExecutionError,
    ExecutionResult,
    FrozenModel,
    Handoff,
    StepOutcome,
    TaskSnapshot,
)
from core.policy import AgentPolicy

if TYPE_CHECKING:
    from infrastructure.storage.store import RecordStore


# ---------------------------------------------------------------------------
# 1. 状态契约定义 (State Definitions)
# ---------------------------------------------------------------------------

class WorkflowState(TypedDict, total=False):
    """任务子图状态：单个 carrier 任务从源码快照到交付的完整通道。"""

    resolution: dict[str, Any]
    step_outcomes: dict[str, Any]
    delivery_candidate: dict[str, Any]
    approval_result: dict[str, Any]
    analysis_report: dict[str, Any]
    workflow_version: int
    validation_recovery_counts: dict[str, int]

    # 身份与执行参数
    run_id: str
    task_id: str
    task: dict[str, Any]
    problem: dict[str, Any]
    source_repo_path: str
    attempt: int
    automatic_attempt: int
    max_attempts: int
    review_mode: str
    allow_push: bool
    require_push: bool
    retry_reason: str
    retry_context: dict[str, Any]
    continuation: dict[str, Any]
    retry_target: str

    # 任务局部产物
    source_project_path: str
    framework: str
    ai_workspace_path: str
    baseline_manifest: dict[str, str]
    baseline_dir: str
    changed_files: list[str]
    repair_changed_files: list[str]
    unified_fixer: dict[str, Any]
    repair: dict[str, Any]
    locate: dict[str, Any]
    review: dict[str, Any]
    evidence: dict[str, Any]
    manual_confirmation_required: bool
    manual_confirmation_reasons: list[str]
    decision: str
    last_result: str
    next_step: str
    terminal_status: str
    change_plan: dict[str, Any]
    blocked_reason: str
    scope_escalation_required: bool
    repair_slot_claimed: bool
    approval_granted: bool
    delivery: dict[str, Any]
    delivery_pushed: bool
    delivery_local_branch: bool
    delivery_commit: str
    repair_branch: str
    approval_cleanup_failed: bool
    approval_recovery_blocked: bool
    task_summary: dict[str, Any]
    analysis_only: bool


class RunState(TypedDict, total=False):
    """父图状态：run 级步骤、任务分发游标与各任务结果汇总。"""

    step_outcomes: dict[str, Any]
    workflow_version: int
    schema_version: int
    run_id: str
    source_repo_path: str
    log_service_name: str
    log_service_prefix: str
    log_excluded_messages: list[str]
    log_excluded_biztypes: list[str]
    log_hours: int
    log_limit: int
    attempt: int
    automatic_attempt: int
    max_attempts: int
    review_mode: str
    allow_push: bool
    require_push: bool
    tasks: list[dict[str, Any]]
    task_cursor: int
    task: dict[str, Any]
    task_id: str
    problem: dict[str, Any]
    terminal_status: str
    task_summary: dict[str, Any]
    task_results: Annotated[list[dict[str, Any]], operator.add]
    last_result: str
    next_step: str


# ---------------------------------------------------------------------------
# 2. 补丁内容身份绑定 (Evidence Version)
# ---------------------------------------------------------------------------

def evidence_version(workspace: str, files: list[str], baseline_dir: str = "") -> dict[str, Any]:
    """对本轮文件及其基线计算内容身份；删除保留显式标记。"""
    def digest(root: str) -> str:
        base = Path(root).resolve()
        values: dict[str, str] = {}
        for name in sorted(set(files)):
            target = (base / name).resolve()
            if not target.is_relative_to(base):
                raise ValueError("证据文件越出工作区")
            values[name] = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else "deleted"
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

    return {
        "patch_id": digest(workspace),
        "baseline_id": digest(baseline_dir) if baseline_dir else "",
        "identity_version": 1,
    }


# ---------------------------------------------------------------------------
# 3. 产物交付与通道交接 (Handoff & State Owners)
# ---------------------------------------------------------------------------

CANDIDATE_KEYS = ("delivery_candidate", "approval_result", "delivery")
INVALIDATES = {
    "prepare_source_snapshot": ("unified_fixer", "locate", "repair", "review", *CANDIDATE_KEYS),
    "unified_fixer_agent": ("review", *CANDIDATE_KEYS),
    "review_agent": CANDIDATE_KEYS,
    "generate_report": ("approval_result", "delivery"),
}
STATE_OWNERS = {
    "baseline_dir": "prepare_source_snapshot", "baseline_manifest": "prepare_source_snapshot",
    "unified_fixer": "unified_fixer_agent", "locate": "unified_fixer_agent", "repair": "unified_fixer_agent",
    "review": "review_agent", "delivery_candidate": "generate_report", "approval_result": "request_approval",
    "delivery": "submit_changes", "repair_branch": "generate_report", "delivery_pushed": "submit_changes",
    "delivery_local_branch": "submit_changes", "delivery_commit": "submit_changes",
    "task_summary": "finalize_execution", "resolution": "finalize_execution",
}

STATE_CO_WRITERS = {
    "delivery_pushed": frozenset({"generate_report"}),
    "delivery_local_branch": frozenset({"generate_report"}),
    "delivery_commit": frozenset({"generate_report"}),
    "delivery": frozenset({"generate_report"}),
    "repair_branch": frozenset({"repair_branch_preflight", "claim_repair_slot"}),
}

EXEMPT_FROM_OBSOLETE = frozenset({"baseline_dir", "baseline_manifest"})


def outcome_of(
    output: dict[str, Any],
    *,
    execution_status: str = "succeeded",
    error: dict[str, Any] | None = None,
    summary: str = "",
    business: str | None = None,
    artifacts: list[str] | None = None,
) -> StepOutcome:
    """将插件业务模型转为统一结果，执行失败绝不视为代码缺陷。"""
    status = output.get("status")
    if execution_status != "succeeded":
        outcome = "inconclusive"
    elif status in {
        "unverified",
        "not_configured",
        "not_executed",
        "syntax_only",
    } or business in {"MANUAL", "INSUFFICIENT_EVIDENCE", "UNVERIFIED", "BLOCKED"}:
        outcome = "inconclusive"
    elif output.get("action") == "blocked" or output.get("stale") is True:
        outcome = "inconclusive"
    elif isinstance(output.get("report"), dict) and output["report"].get("results"):
        statuses = {item.get("status") for item in output["report"]["results"]}
        outcome = (
            "failed"
            if "failed" in statuses
            else "inconclusive"
            if "unverified" in statuses
            else "passed"
        )
    elif business == "SKIPPED":
        outcome = "not_applicable"
    elif output.get("passed") is False or business in {"FAIL", "FAILED", "REJECTED"}:
        outcome = "failed"
    else:
        outcome = "passed"
    return StepOutcome(
        execution=ExecutionResult(
            status=execution_status,
            error=ExecutionError.model_validate(error) if error else None,
        ),
        assessment=Assessment(
            outcome=outcome,
            code=str(output.get("reason_code") or business or status or outcome),
            summary=summary or str(output.get("reason") or output.get("summary") or ""),
            findings=[str(item) for item in output.get("findings", [])],
        ),
        output=output,
        artifact_paths=artifacts or [],
    )


def apply_step_result(
    store: Any,
    state: dict[str, Any],
    step: str,
    patch: dict[str, Any],
    outcome: StepOutcome,
) -> dict[str, Any]:
    """先保存无损交接，再发布图更新；摘要记录不承担恢复职责。"""
    for key, value in patch.items():
        if (
            key in STATE_OWNERS
            and STATE_OWNERS[key] != step
            and step not in STATE_CO_WRITERS.get(key, ())
            and not (step == "retry_prepare" and (not value or value == state.get(key)))
        ):
            raise ValueError(f"STATE_OWNER_MISMATCH: {step} 不能写入 {key}")
    invalidated = {key: {} for key in INVALIDATES.get(step, ()) if key not in patch}
    if any(key in invalidated for key in CANDIDATE_KEYS):
        invalidated.update(approval_granted=False, decision="PENDING")
    patch = {**invalidated, **patch}
    if step != "finalize_execution":
        patch.update(resolution=None, task_summary={})
    current = {**state, **patch}
    current.pop("step_result", None)
    current.pop("_single_step", None)
    obsolete = {
        STATE_OWNERS[key]
        for key in patch
        if key in STATE_OWNERS
        and key not in EXEMPT_FROM_OBSOLETE
        and not patch[key]
    }
    outcomes = {
        key: value
        for key, value in (state.get("step_outcomes") or {}).items()
        if key not in obsolete
    }
    outcomes[step] = outcome.model_dump(mode="json")
    current["step_outcomes"] = outcomes
    run_id, task_id = str(state["run_id"]), str(state.get("task_id") or "")
    handoff = Handoff(
        step=step, outcome=outcome, state=TaskSnapshot.from_graph(current)
    )
    path = f"workflow/outputs/{step}.json"
    store.write_artifact(run_id, path, handoff.model_dump(mode="json"), task_id)
    store.write_artifact(
        run_id, "workflow/state.json", handoff.model_dump(mode="json"), task_id
    )
    return {**patch, "step_outcomes": outcomes}


# ---------------------------------------------------------------------------
# 4. 独立任务上下文与调度 (Task Execution)
# ---------------------------------------------------------------------------

def task_config(run_id: str, task_id: str, recursion_limit: int = 1000) -> dict[str, Any]:
    """返回单个任务的稳定独立 checkpoint 标识。"""
    return {
        "configurable": {"thread_id": f"{run_id}:task:{task_id}"},
        "recursion_limit": recursion_limit,
    }


def invoke_task(
    graph: CompiledStateGraph, value: Any, config: dict[str, Any],
) -> dict[str, Any]:
    """在父图的 LangGraph 上下文之外以线程隔离方式执行任务子图。"""
    from workflow.engine.routes import assert_workflow_version

    snapshot = graph.get_state(config) if graph.checkpointer else None
    if snapshot is not None and snapshot.values:
        assert_workflow_version(snapshot.values)
    elif isinstance(value, dict):
        assert_workflow_version(value)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="workflow-task") as pool:
        return pool.submit(graph.invoke, value, config=config).result()


def task_result(state: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """过滤子图执行结果，仅将运行汇总写回父图通道。"""
    if result.get("__interrupt__"):
        interrupts = result["__interrupt__"]
        if any(
            not isinstance(item.value, dict) or item.value.get("kind") != "change_approval"
            for item in interrupts
        ):
            raise GraphInterrupt(interrupts)
        task = dict(state.get("task") or {})
        return {
            "terminal_status": "WAITING_APPROVAL",
            "last_result": "waiting",
            "task_summary": {
                "task_id": str(state["task_id"]),
                "carrier": str(task.get("carrier") or ""),
                "biztype": str(task.get("biztype") or ""),
                "result": "WAITING_APPROVAL",
            },
        }
    return {key: result.get(key) for key in ("terminal_status", "last_result", "task_summary")}


# ---------------------------------------------------------------------------
# 5. 重试上下文与证据捕获 (Retry Context)
# ---------------------------------------------------------------------------

def _sanitize(value: Any) -> Any:
    return deepcopy(value)


def _compact(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        text = value
        return text[:1000] + ("…（完整内容见重试产物）" if len(text) > 1000 else "")
    if depth >= 4:
        return "详见重试产物"
    if isinstance(value, dict):
        return {str(key): _compact(item, depth + 1) for key, item in list(value.items())[:12]}
    if isinstance(value, list):
        return [_compact(item, depth + 1) for item in value[:8]]
    return value


def capture_retry_context(
    state: dict[str, Any], store: RecordStore | None = None, *, reuse_existing: bool = True
) -> dict[str, Any]:
    """落盘失败尝试证据，并返回有界的上下文字典。"""
    attempt = int(state.get("attempt") or 1)
    if store is not None and reuse_existing:
        latest_path = store.task_dir(str(state["run_id"]), str(state["task_id"])) / "retry/latest.json"
        if latest_path.is_file():
            captured = store.read_artifact(str(state["run_id"]), "retry/latest.json", str(state["task_id"]))
            if captured.get("attempt") == attempt and not captured.get("capture_errors"):
                return captured
    paths = sorted(
        set(state.get("changed_files") or (state.get("repair") or {}).get("changed_files") or [])
    )
    payload = {
        "attempt": attempt,
        "retry_target": str(state.get("retry_target") or ""),
        "failure_reason": str(state.get("retry_reason") or ""),
        "changed_files": paths,
        **{
            key: state.get(key) or {}
            for key in (
                "unified_fixer",
                "locate",
                "repair",
                "change_plan",
                "review",
            )
        },
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
                candidates.update(
                    path.relative_to(workspace).as_posix()
                    for path in carrier_root.rglob("*")
                    if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
                )
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
            diff += "".join(
                difflib.unified_diff(
                    old.splitlines(True),
                    new.splitlines(True),
                    fromfile=f"baseline/{relative}",
                    tofile=f"attempt-{attempt}/{relative}",
                )
            )
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
        store.write_artifact(str(state["run_id"]), artifact, _sanitize(payload), str(state["task_id"]))
    previous = state.get("retry_context") or {}
    history = list(previous.get("history") or [])
    if previous.get("attempt"):
        history.append({key: previous.get(key) for key in ("attempt", "failure_reason", "changed_files")})
    context = {
        "attempt": attempt,
        "failure_reason": _compact(payload["failure_reason"]),
        "changed_files": paths[:16],
        "artifact": artifact,
        "history": _compact(history[-3:]),
    }
    context["capture_errors"] = capture_errors
    for key in (
        "unified_fixer",
        "locate",
        "repair",
        "change_plan",
        "review",
    ):
        value = _compact(payload[key])
        encoded = json.dumps(value, ensure_ascii=False, default=str)
        context[key] = value if len(encoded) <= 1600 else encoded[:1600] + "…（详见重试产物）"
    context["diff"] = diff[:4000]
    length = len(json.dumps(context, ensure_ascii=False, default=str))
    if length > 12000:
        context["diff"] = context["diff"][:max(0, 4000 - (length - 12000) - 100)]
        context["history"] = _compact(context["history"][-1:])
        for key in (
            "unified_fixer",
            "locate",
            "repair",
            "change_plan",
            "review",
        ):
            encoded = json.dumps(context[key], ensure_ascii=False, default=str)
            if len(encoded) > 700:
                context[key] = encoded[:600] + "…（详见重试产物）"
        context["context_truncated"] = True
    context = _sanitize(context)
    if store is not None:
        store.write_artifact(str(state["run_id"]), "retry/latest.json", context, str(state["task_id"]))
    return context


def stage_retry_evidence(store: RecordStore, state: dict[str, Any]) -> dict[str, Any]:
    """将任务历史证据安全投影到只读工作区目录。"""
    context = dict(state.get("retry_context") or {})
    artifact = str(context.get("artifact") or "")
    if not artifact:
        return context
    if not re.fullmatch(r"retry/attempt-\d+(?:-manual-[0-9a-f]+)?\.json", artifact):
        raise ValueError("重试产物路径不符合任务证据格式")
    from core.policy import retry_evidence_dir

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


# ---------------------------------------------------------------------------
# 6. 前端契约生成 (Frontend Contracts)
# ---------------------------------------------------------------------------

def ts_type(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if schema.get("type") == "object":
        value = schema.get("additionalProperties")
        return f"Record<string, {ts_type(value)}>" if isinstance(value, dict) else "Record<string, unknown>"
    if "enum" in schema:
        return " | ".join(json.dumps(value) for value in schema["enum"])
    if "anyOf" in schema:
        return " | ".join(ts_type(value) for value in schema["anyOf"])
    if schema.get("type") == "array":
        return f"Array<{ts_type(schema['items'])}>"
    return {
        "string": "string",
        "boolean": "boolean",
        "integer": "number",
        "number": "number",
        "null": "null",
    }.get(schema.get("type"), "unknown")


def generate() -> str:
    from server.projections import task_stages
    from server.view_models import ActionAvailability, DeliveryView

    definitions = {}
    for model in (ExecutionResult, Assessment, ActionAvailability, DeliveryView, AgentPolicy):
        schema = model.model_json_schema()
        definitions.update(schema.pop("$defs", {}))
        definitions[model.__name__] = schema
    lines = ["// Generated by python -m workflow.engine.frontend_contracts. Do not edit."]
    for name, schema in definitions.items():
        lines.append(f"export interface {name} {{")
        required = set(schema.get("required", []))
        for key, value in schema["properties"].items():
            optional = "" if key in required else "?"
            lines.append(f"  {key}{optional}: {ts_type(value)};")
        lines.append("}\n")
    lines.append("export const WORKFLOW_STAGES = " + json.dumps(task_stages(), ensure_ascii=False, indent=2) + ";\n")
    return "\n".join(lines)


def main() -> None:
    output = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "workflow.generated.ts"
    output.write_text(generate(), encoding="utf-8")
    print(f"Contracts written to {output}")


if __name__ == "__main__":
    main()


__all__ = [
    "CANDIDATE_KEYS",
    "EXEMPT_FROM_OBSOLETE",
    "INVALIDATES",
    "RunState",
    "STATE_CO_WRITERS",
    "STATE_OWNERS",
    "WorkflowState",
    "apply_step_result",
    "capture_retry_context",
    "evidence_version",
    "generate",
    "invoke_task",
    "main",
    "outcome_of",
    "stage_retry_evidence",
    "task_config",
    "task_result",
    "ts_type",
]
