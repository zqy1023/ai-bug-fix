"""审查子图。

单节点 ``review``。与修复子图不共享状态、配置和 provider。
对外只留 verdict / passed / summary / findings / required_changes。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from app.infrastructure.filesystem.workspace import WorkspaceManager
from app.agents.context import build_context
from app.agents.review.contract import judge, slim_review
from app.agents.review.config import ReviewConfig
from app.application.plans import plan_of
from app.agents.review.models import ReviewView
from app.agents.review.provider import create_provider
from app.agents.review.state import ReviewState
from app.agents.common import audit_metadata, parse_result, raw_result, usage_of
from app.infrastructure.filesystem.diff import file_changes
from app.prompts import PromptRenderer
from app.prompts.context import model_name, prompt_context
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph


def _lines(value: Any) -> list[str]:
    """清单收成短句，不把原始结构带回主图。"""
    if not isinstance(value, list):
        return []
    lines: list[str] = []
    for item in value[:8]:
        if isinstance(item, str):
            lines.append(item)
        elif isinstance(item, dict):
            lines.append(str(item.get("summary") or item.get("message") or "")[:300])
    return [item for item in lines if item]


_CONTROL_FLOW = ("if ", "elif ", "else:", "for ", "while ", "try:", "except", "raise ", "return ")


def _ast_status(root: Path, relative: str) -> str:
    """对改动后的 Python 文件做一次语法解析，给审查一个客观结论。

    文件不存在（例如门禁判定为删除）时返回 ``n/a``，不能当成语法失败。
    """
    try:
        source = (root / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "n/a"
    try:
        ast.parse(source)
    except SyntaxError:
        return "failed"
    return "ok"


def _carrier_has_tests(root: Path, carrier: str) -> bool:
    """目标 carrier 下是否存在任何测试文件（用于覆盖软信号）。"""
    try:
        roots = WorkspaceManager.locate_carrier_roots(root, carrier)
    except Exception:  # noqa: BLE001 - 定位失败只削弱软信号，不应中断审查
        return False
    for relative in roots:
        base = root / relative
        for path in base.rglob("*.py"):
            name = path.name.lower()
            parts = {part.lower() for part in path.relative_to(base).parts[:-1]}
            if (
                name.startswith("test_")
                or name.endswith("_test.py")
                or any(part == "tests" or part.startswith("test_") or part == "test" for part in parts)
            ):
                return True
    return False


def _gate_entries(state: dict[str, Any]) -> list[dict[str, Any]]:
    """取确定性门禁给出的逐文件结论。

    门禁组合节点只把「通过与否」写进 steps.json，逐文件证据不进记录；离线单跑审查、
    或缺少基线副本时，就靠状态里带过来的门禁结论。这是与现场重算等价的可信来源。
    """
    gate = state.get("gate_result")
    if not isinstance(gate, dict):
        return []
    files = gate.get("files")
    if not isinstance(files, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in files:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        entries.append({
            "path": str(item["path"]),
            "status": str(item.get("status") or "modified"),
            "added": _safe_int(item.get("added")),
            "removed": _safe_int(item.get("removed")),
            "lines": list(item.get("lines") or []),
        })
    return entries


def _safe_int(value: Any) -> int:
    """把门禁计数安全收敛为整数，避免异常记录击穿审查节点。"""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _evidence_block(state: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """构造确定性证据块。

    审查要判断"这次改动对不对"，必须拿到逐文件差异。证据来源按可信度二选一：
    先与门禁同源现场重算（基线副本 + ``file_changes``），缺基线时回退到状态里
    带过来的门禁逐文件结论；两者都没有就明确写"不可用"，不让审查凭空判断。
    """
    workspace = str(state.get("ai_workspace_path") or "")
    baseline_dir = str(state.get("baseline_dir") or "")
    declared = [str(item) for item in (state.get("changed_files") or [])]
    root = Path(workspace) if workspace else None

    def flag(value: Any) -> str:
        return "通过" if value else "未通过"

    lines: list[str] = [
        (
            f"- 确定性门禁：{flag(state.get('check_passed'))}；"
            f"语法回归：{flag(state.get('syntax_passed'))}；"
            f"业务验证状态：{state.get('business_verification_status') or 'unknown'}；"
            f"回归：{flag(state.get('regression_passed'))}"
        )
    ]

    changes: list[dict[str, Any]] = []
    source = ""
    if root is not None and baseline_dir and declared:
        try:
            changes = file_changes(baseline_dir, workspace, declared)
        except OSError:
            changes = []
        if changes:
            source = "基线比对"
    if not changes:
        changes = _gate_entries(state)
        if changes:
            source = "确定性门禁"

    if declared and (baseline_dir or source):
        known = {str(item.get("path")) for item in changes}
        phantom = sorted(set(declared) - known)
        lines.append(
            "- 声明与真实差异：一致"
            if not phantom
            else f"- 声明与真实差异：不一致（声明但无变化：{', '.join(phantom)}）"
        )
    if source:
        lines.append(f"- 逐文件差异来源：{source}")

    added_control = 0
    secret_warnings = 0
    for item in changes[:12]:
        relative = str(item.get("path") or "")
        status = str(item.get("status") or "")
        added = _safe_int(item.get("added"))
        removed = _safe_int(item.get("removed"))
        if root is not None and relative.endswith(".py"):
            ast_state = _ast_status(root, relative)
        else:
            ast_state = "n/a"
        suffix = f"（AST={ast_state}）" if ast_state != "n/a" else ""
        lines.append(f"- {relative}：status={status}，新增 {added} 行，删除 {removed} 行{suffix}")

        # 敏感信息属于确定性安全门禁：命中时不允许仅靠 Agent 语义判断放行。
        secret_hits = int(item.get("secret_hits") or 0)
        if secret_hits > 0:
            secret_warnings += secret_hits
            samples = item.get("secret_samples") or []
            lines.append(f"  ⚠️ 检测到 {secret_hits} 处疑似敏感信息（需语义判断）：")
            for sample in samples[:3]:  # 最多显示3个样本
                if not isinstance(sample, dict):
                    continue
                line_no = sample.get("line", "")
                snippet = sample.get("snippet", "")
                sample_source = sample.get("source", "")
                lines.append(f"    - 第{line_no}行 ({sample_source}): {snippet[:80]}")
            if len(samples) > 3:
                lines.append(f"    - ...另有 {len(samples) - 3} 处")

        shown = 0
        for line in item.get("lines") or []:
            if not isinstance(line, dict):
                continue
            kind = line.get("type")
            if kind not in {"add", "del"}:
                continue
            text = str(line.get("text") or "").rstrip()
            if not text.strip():
                continue
            if kind == "add" and any(keyword in text for keyword in _CONTROL_FLOW):
                added_control += 1
            lines.append(f"    {'新增' if kind == 'add' else '删除'}：{text}")
            shown += 1
        if item.get("truncated"):
            lines.append("    差异列表未完整展示；必须读取当前文件及对应只读基线后再作代码结论。")

    if secret_warnings > 0:
        lines.append(
            f"- 安全门禁失败：检测到 {secret_warnings} 处疑似硬编码敏感信息；必须先移除或替换为安全配置，不能通过语义解释绕过。"
        )

    if declared and not changes:
        lines.append("- 逐文件差异：不可用（缺少基线副本，门禁也未提供逐文件结论）")

    if added_control > 0 and root is not None and not _carrier_has_tests(root, str(state.get("carrier") or "")):
        lines.append(
            f"- 覆盖提示：本轮新增了 {added_control} 处控制流/分支，但目标 carrier 下没有测试文件，"
            "无法用回归验证覆盖；请在结论中说明这些分支的验证依据，必要时在 required_changes 中要求补最小验证。"
        )
    return "\n".join(lines), {"secret_warnings": secret_warnings}


def _bounded_evidence(text: str, max_chars: int) -> str:
    """按行裁剪审查证据，保留明确截断标记，避免把超大 diff 重复塞进 Prompt。"""
    if len(text) <= max_chars:
        return text
    marker = "\n- [证据预算限制] 后续差异未全部注入模型上下文；不得推断未展示内容。"
    budget = max(256, max_chars - len(marker))
    lines: list[str] = []
    used = 0
    for line in text.splitlines():
        cost = len(line) + (1 if lines else 0)
        if used + cost > budget:
            break
        lines.append(line)
        used += cost
    return "\n".join(lines) + marker


def review(state: ReviewState, runtime: Any) -> dict[str, Any]:
    """只读审查。"""
    workspace = str(state["ai_workspace_path"])
    carrier = str(state["carrier"])
    framework = str(state["framework"])
    if framework not in {"gathers", "spiders"}:
        raise ValueError("审查输入缺少确定性框架")
    carrier_root = WorkspaceManager.find_carrier_dir(workspace, framework, carrier)
    if carrier_root is None:
        raise ValueError(f"工作区缺少审查目标 carrier: {carrier}")
    carrier_dir = carrier_root.relative_to(Path(workspace).resolve()).as_posix()
    config = getattr(runtime, "config", None)
    config = config if isinstance(config, ReviewConfig) else ReviewConfig.load()
    changed_files = [str(item) for item in state.get("changed_files", [])]
    from app.workflow.evidence_version import evidence_version
    import hashlib
    from app.workflow.steps.check_changes import verification
    current_version = evidence_version(workspace, changed_files, str(state.get("baseline_dir") or ""))
    current_policy = hashlib.sha256(Path(verification.__file__).read_bytes()).hexdigest()
    for name in ("gate_result", "verify_result", "regression_result"):
        recorded = (state.get(name) or {}).get("evidence_version") or {}
        if recorded and (recorded.get("patch_id") != current_version["patch_id"] or (
            name == "gate_result" and recorded.get("policy_id") != current_policy
        )):
            return {**ReviewView(verdict="MANUAL", passed=False, summary=f"{name} 对应的源码或门禁版本已变化，需要重跑验证",
                                required_changes=["重新执行变更检查及后续验证"], evidence_version=current_version).model_dump(),
                    "raw_result": "", "_usage": {}, "_audit": {"blocked_by": "stale_evidence"}}
    evidence_text, evidence_meta = _evidence_block(
        {**state, "ai_workspace_path": workspace, "changed_files": changed_files}
    )
    from app.agents.review.boundaries import datetime_reset_risks, not_found_guard_changes
    classification_boundaries = not_found_guard_changes(workspace, str(state.get("baseline_dir") or ""), changed_files)
    datetime_boundaries = datetime_reset_risks(workspace, str(state.get("baseline_dir") or ""), changed_files)
    full_diff_summary = str(state.get("diff_summary") or "")
    deterministic_context = build_context(
        dict(state["problem"]),
        changed_files,
        str(state.get("diff_summary") or ""),
        max_chars=config.max_context_chars,
        max_candidates=config.max_candidate_files,
    )
    from app.agents.policy import review_read_paths
    shared_root = Path(str(state["source_project_path"])).resolve() if state.get("source_project_path") else None
    roots = tuple(str(path) for path in review_read_paths(Path(workspace), carrier_root, shared_root, carrier,
                                                        str(state.get("baseline_dir") or "")))
    from app.agents.policy import retry_read_paths
    roots = (*roots, *(str(path) for path in retry_read_paths(Path(workspace), str(state["run_id"]), str(state["task_id"]))))
    request_context = prompt_context(
        "review",
        state,
        framework=framework,
        evidence={
            "evidence_version": current_version,
            "diff_and_gate_evidence": evidence_text,
            "classification_boundaries": classification_boundaries,
            "datetime_boundaries": datetime_boundaries,
            "deterministic_context": deterministic_context,
            "changed_files": changed_files,
            "repair_plan": dict((state.get("repair") or {}).get("change_plan") or {}),
            "validation_summary": dict(state.get("validation_summary") or {}),
            "check_passed": bool(state.get("check_passed")),
            "syntax_passed": bool(state.get("syntax_passed")),
            "verify_passed": bool(state.get("verify_passed")),
            "business_verification_status": str(state.get("business_verification_status") or "unknown"),
            "regression_passed": bool(state.get("regression_passed")),
        },
        read_roots=roots,
        write_roots=(),
        budget={"max_tool_calls": config.max_tool_calls, "max_turns": config.max_turns,
                "max_context_chars": config.max_context_chars},
        source_snapshot={
            "baseline_dir": str(state.get("baseline_dir") or ""),
            "baseline_manifest": dict(state.get("baseline_manifest") or {}),
        },
        changed_files=tuple(changed_files),
        diff_summary=full_diff_summary,
        gate_results=dict(state.get("gate_result") or {}),
        verification_results=dict(state.get("verify_result") or {}),
        regression_results=dict(state.get("regression_result") or {}),
        allowed_scope=(carrier_dir,),
        stop_conditions=("必需验证证据缺失", "真实 diff 与声明不一致", "发现未授权范围"),
        source_project_path=str(shared_root) if shared_root else "",
    )
    request = PromptRenderer().request(
        "review",
        request_context,
        stage="review",
        workspace=workspace,
        model=model_name(runtime),
    )
    if evidence_meta.get("secret_warnings", 0) > 0:
        return {
            "verdict": "FAIL", "passed": False,
            "summary": "确定性安全门禁阻断：检测到疑似硬编码敏感信息",
            "code_verdict": "FAIL",
            "findings": ["检测到疑似敏感信息，需移除硬编码凭据或替换为安全配置"],
            "required_changes": ["修复敏感信息命中后重新执行确定性检查"],
            "change_plan": {}, "raw_result": "", "_usage": {},
            "_audit": {"tool_call_count": 0, "blocked_by": "secret_gate"},
        }
    raw = runtime.run(request)
    if evidence_version(workspace, changed_files, str(state.get("baseline_dir") or "")) != current_version:
        return {**ReviewView(verdict="MANUAL", passed=False, summary="审查期间源码变化，结论无效，需重跑验证和审查",
                             required_changes=["重新执行变更检查、问题验证及审查"],
                             evidence_version=current_version).model_dump(),
                "raw_result": "", "_usage": {}, "_audit": {"blocked_by": "source_changed_during_review"}}
    raw = raw if isinstance(raw, dict) else {}
    original = raw_result(raw)
    usage = usage_of(raw)
    # 调用层失败和无返回不属于模型 verdict 协议错误，也不能认定代码有缺陷。
    # 部分测试/人工 provider 直接返回业务对象，不能将这类对象误当作空正文。
    direct_payload = "result" not in raw and ("verdict" in raw or "findings" in raw)
    provider_failed = raw.get("passed") is False and bool(raw.get("reason")) and not direct_payload
    if provider_failed or (not original.strip() and not direct_payload):
        from app.agents.review.checklist import review_checks

        category = (str(raw.get("failure_kind") or "") or ("agent_timeout" if raw.get("reason") == "AGENT_TIMEOUT" else "provider_error")) if provider_failed else "empty_response"
        summary = ("审查调用超时，未取得有效审查结论" if category == "agent_timeout" else
                   "审查工具放行预算耗尽，未取得有效结论" if category == "tool_budget_exhausted" else
                   "审查连续遇到权限或重复操作拒绝，已停止无效调用" if category == "tool_access_blocked" else
                   "审查调用失败，未取得有效审查结论" if provider_failed else "审查未返回结果，未取得有效审查结论")
        # reason 可能包含连接串或 SDK 正文，仅持久化受控类别。
        return {
            **ReviewView(verdict="MANUAL", passed=False, delivery_eligible=False,
                         summary=summary, code_verdict="UNKNOWN",
                         required_changes=["重新执行 AI 审查；若持续失败，检查模型服务调用记录"],
                         evidence_version=current_version, checks=review_checks(state, "UNKNOWN", {}),
                         next_action="重新执行 AI 审查，取得有效结论后再决定交付").model_dump(),
            "raw_result": original, "_usage": usage,
            "_audit": {**audit_metadata(request, usage), "failure_stage": "invocation", "error_type": category},
        }
    payload = parse_result(raw, "review")
    slim = slim_review(payload)
    findings = _lines(slim.get("findings"))
    required = _lines(slim.get("required_changes"))
    checked = judge(payload)
    from app.agents.review.contract import result_of
    result = result_of(payload)
    verdict = "MANUAL" if checked.valid and result == "MANUAL" else ("PASS" if checked.passed else "FAIL")
    coverage = dict(payload.get("coverage") or {}) if checked.valid else {}
    required = list(dict.fromkeys(required))
    plan = plan_of(payload)
    from app.agents.review.checklist import review_checks
    code_verdict = str(payload.get("code_verdict") or "UNKNOWN").upper()
    if code_verdict not in {"PASS", "FAIL", "UNKNOWN"}:
        code_verdict = "UNKNOWN"
    if checked.valid and code_verdict == "FAIL":
        verdict = "FAIL"
    checks = review_checks(state, code_verdict, coverage)
    next_action = "由统一决策节点根据验证事实和代码审查决定下一步"
    view = ReviewView(
        verdict=verdict,
        passed=verdict == "PASS",
        delivery_eligible=False,
        contract_error="" if checked.valid else checked.reason,
        evidence_version=current_version,
        code_verdict=code_verdict,
        coverage=coverage,
        checks=checks,
        next_action=next_action,
        review_notes=str(slim.get("summary") or ""),
        summary=str(slim.get("summary") or checked.reason) if checked.valid else f"审查输出协议错误：{checked.reason}",
        findings=findings,
        required_changes=required,
        change_plan=plan.model_dump(mode="json") if plan is not None else {},
        acceptance_results=list(slim.get("acceptance_results") or []),
    )
    return {
        **view.model_dump(),
        "raw_result": original,
        "_usage": usage,
        "_audit": audit_metadata(request, usage),
    }


def build(runtime: Any | None = None) -> CompiledStateGraph:
    """编译审查子图。"""
    resolved = runtime or create_provider_from_env()

    def node(state: ReviewState) -> dict[str, Any]:
        return review(state, resolved)

    graph: StateGraph = StateGraph(ReviewState)
    graph.add_node("review", node)
    graph.add_edge(START, "review")
    graph.add_edge("review", END)
    return graph.compile()


def create_provider_from_env() -> Any:
    """按审查目录自己的配置创建 provider。"""
    from app.agents.review.config import ReviewConfig

    config = ReviewConfig.load()
    return create_provider(config.provider, config)


__all__ = ["build", "review"]
