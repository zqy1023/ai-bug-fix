"""统一分析修复 Agent 子图与单会话编排。"""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from infrastructure.llm.common import audit_metadata, parse_result, raw_result, usage_of
from core.policy import carrier_read_paths, carrier_write_paths, retry_read_paths, rules_text
from agents.unified_fixer.models import UnifiedFixerConfig, UnifiedFixerInput, UnifiedFixerOutput, UnifiedFixerState
from agents.unified_fixer.provider import create_provider
from core.policy import legacy_analysis_required
from infrastructure.workspace.workspace import WorkspaceError, WorkspaceManager
from infrastructure.storage.redaction import public_exception
from infrastructure.workspace.diff import source_manifest, source_changes
from infrastructure.llm import PromptRenderer
from infrastructure.llm.rules import budget_tier, select_rules
from infrastructure.llm.context import model_name, prompt_context
from infrastructure.workspace.reproduction import needs_reproduction, reproduce_before_analysis

logger = logging.getLogger(__name__)


def _ast_check_files(workspace_root: Path, changed_files: list[str], baseline_dir: str = "") -> tuple[bool, str]:
    """对模型声称修改的文件进行确定性 AST 静态语法解析校验。"""
    for rel in changed_files:
        path = (workspace_root / rel).resolve()
        if not path.is_relative_to(workspace_root.resolve()):
            return False, f"文件越出工作区: {rel}"
        if not path.is_file():
            if baseline_dir and (Path(baseline_dir) / rel).is_file():
                continue  # 基线中存在才是实际删除。
            return False, f"声明的变更文件不存在: {rel}"
        if path.suffix != ".py":
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8-sig"))
        except SyntaxError as exc:
            return False, f"文件 {rel} 存在语法错误 (Line {exc.lineno}): {exc.msg}"
        except Exception as exc:
            return False, f"文件 {rel} 读取或解析失败: {exc}"
    return True, ""


def run_unified_fixer(state: UnifiedFixerState, runtime: Any) -> dict[str, Any]:
    """单会话端到端完成：异常感知建模 -> 假设驱动定位 -> 准入裁决 -> 受限修补 -> AST 自检反思。"""
    workspace = Path(str(state.get("workspace_path") or state.get("ai_workspace_path") or "")).resolve()
    carrier = str(state.get("carrier") or "")
    run_id = str(state.get("run_id") or "")
    task_id = str(state.get("task_id") or "")
    problem = dict(state.get("problem") or {})
    evidence = dict(state.get("evidence") or {})
    source_snapshot = dict(state.get("source_snapshot") or {})
    framework = str(evidence.get("framework") or "")
    reproduction: dict[str, Any] = {}

    # 1. 检查旧框架只读红线与准入
    is_legacy = legacy_analysis_required(state) or framework == "spiders"
    from core.policy import policy_of
    if is_legacy and not policy_of(state).analyze_legacy:
        return _build_step_result({"status": "ANALYSIS_ONLY", "result": "passed", "passed": False,
            "skip_reason": "策略关闭旧框架分析", "changed_files": [], "facts": ["当前目标属于只读旧框架，策略未启用分析"],
            "summary": "策略关闭旧框架分析"}, {}, {})
    shared_root_val = state.get("source_project_path")
    shared_root = Path(str(shared_root_val)).resolve() if shared_root_val and Path(str(shared_root_val)).is_dir() else None

    carrier_root = WorkspaceManager.find_carrier_dir(str(workspace), framework, carrier)

    # 构造读写白名单
    readable: list[str] = []
    if carrier_root is not None:
        readable.extend(str(p) for p in carrier_read_paths(workspace, carrier_root, shared_root, carrier))
    readable.extend(str(p) for p in retry_read_paths(workspace, run_id, task_id))
    read_roots = tuple(dict.fromkeys(readable))

    write_roots = ()
    write_target = None
    if not is_legacy and carrier_root is not None:
        write_roots = tuple(carrier_write_paths(workspace, carrier))
        write_target = carrier_root

    # 2. 准备 Prompt 上下文
    config = runtime if isinstance(runtime, UnifiedFixerConfig) else getattr(runtime, "config", None)
    config = config if isinstance(config, UnifiedFixerConfig) else UnifiedFixerConfig.load()

    # 预算与规则按证据充分度分级：证据越足，轮次/上下文越小，注入的规则越少。
    blame = str(problem.get("blame") or "")
    tier = budget_tier(
        blame=blame,
        frame=str(problem.get("frame") or ""),
        has_response=bool(problem.get("response")),
        candidate_count=len(list(evidence.get("candidates") or [])),
        field=str(problem.get("field") or ""),
        analysis_only=is_legacy,
    )
    # 档位作为上限，不覆盖用户显式收紧的配置：min(配置, 档位)。
    config = config.model_copy(update={
        "max_turns": min(config.max_turns, tier.max_turns),
        "max_tool_calls": min(config.max_tool_calls, tier.max_tool_calls),
        "max_context_chars": min(config.max_context_chars, tier.max_context_chars),
        "timeout_seconds": min(config.timeout_seconds, tier.timeout_seconds),
        "max_self_healing_turns": 0 if is_legacy else config.max_self_healing_turns,
        "max_provider_attempts": 1 if is_legacy else config.max_provider_attempts,
    })
    rule_ids = select_rules(role="unified_fixer", framework=framework, problem=problem, evidence=evidence)
    # 小任务用精简角色提示词：位置已知时不需要再教它怎么探索。
    role_variant = "analysis" if is_legacy else "small" if tier.name == "small" else ""

    policy = state.get("agent_policy") or {}
    chosen_model = policy.get("unified_model") or getattr(config, "model", "") or model_name(runtime)
    active_prompt_context = prompt_context(
        "unified_fixer", state,
        framework=framework, evidence=evidence,
        read_roots=read_roots,
        write_roots=tuple(str(p) for p in write_roots),
        budget={
            "max_turns": config.max_turns,
            "max_tool_calls": config.max_tool_calls,
            "max_context_chars": config.max_context_chars,
        },
        source_snapshot=source_snapshot,
        retry_feedback=str(state.get("retry_reason") or ""),
        source_project_path=str(shared_root) if shared_root else "",
        project_rules=rules_text(*([carrier_root] if carrier_root else []), *([shared_root] if shared_root else [])),
    )
    request = PromptRenderer().request(
        "unified_fixer", active_prompt_context,
        stage="unified_fixer", workspace=str(workspace), model=chosen_model,
        write_root=str(write_target) if write_target else None,
        rule_ids=rule_ids, budget_tier=tier.name, role_variant=role_variant,
    )

    provider = create_provider(config, runner=getattr(runtime, "agent_runner", None))
    baseline_dir = str(source_snapshot.get("baseline_dir") or "")
    before = source_manifest(baseline_dir) if baseline_dir else source_manifest(workspace)
    calls: list[dict[str, Any]] = []
    healing_attempts = 0
    declared: list[str] = []
    parsed: dict[str, Any] = {}
    syntax_ok, syntax_err = False, "Agent 未完成"
    try:
        # 补充证据最多一次，不占用语法自愈次数；所有任务先由模型核对已有日志与源码。
        for index in range(config.max_self_healing_turns + 2):
            raw_output = provider(request)
            calls.append({"usage": usage_of(raw_output), "audit": audit_metadata(request, usage_of(raw_output))})
            parsed = parse_result(raw_output, "unified_fixer")
            declared = [str(item) for item in parsed.get("changed_files") or []]
            changed_files = source_changes(workspace, before)
            if parsed.get("skip_reason") and (
                parsed.get("status") != "ANALYSIS_ONLY"
                or parsed.get("attribution_type") not in {"official", "environment"}
                or changed_files or declared or parsed.get("proposed_edit_files")
                or (parsed.get("change_plan") or {}).get("edits")
            ):
                parsed.update(skip_reason="", status="MANUAL", result="failed",
                              failure_kind="invalid_skip_disposition",
                              blocked_reason="跳过结论与责任归因或实际源码修改矛盾，不能作为已跳过交付",
                              summary="跳过结论与责任归因或实际源码修改矛盾，不能作为已跳过交付")
                break
            usage = usage_of(raw_output)
            stop_kind = str(usage.get("tool_stop_kind") or "")
            if ((usage.get("tool_blocked_writes") and not changed_files)
                    or (stop_kind and (not changed_files or parsed.get("status") != "FIXED"))):
                # 结构化分析是保留的证据，不能掩盖平台阻断修复的执行事实。
                parsed.update(failure_kind=stop_kind or "tool_write_blocked", result="failed",
                              blocked_reason=str(usage.get("tool_stop_reason") or "工具写入被拒绝，修复未完成"))
            unauthorized = [name for name in changed_files if not any(
                (workspace / name).resolve().is_relative_to(Path(allowed).resolve()) for allowed in write_roots
            )]
            if unauthorized:
                parsed.update(status="MANUAL", scope_escalation_required=True,
                              blocked_reason=f"发现未授权源码变化: {', '.join(unauthorized)}")
                syntax_ok, syntax_err = False, str(parsed["blocked_reason"])
                break
            if (not reproduction and not changed_files
                    and parsed.get("status") == "INSUFFICIENT_EVIDENCE"
                    and not parsed.get("failure_kind") and needs_reproduction(problem)):
                reproduction = reproduce_before_analysis(dict(state))
                artifacts = workspace / "artifacts"
                artifacts.mkdir(parents=True, exist_ok=True)
                (artifacts / "reproduction.json").write_text(
                    json.dumps(reproduction, ensure_ascii=False, indent=2), encoding="utf-8")
                if not reproduction.get("reproduced"):
                    reason = str(reproduction["reason"])
                    parsed.update(blocked_reason=reason, summary=reason,
                                  evidence_gaps=[*(parsed.get("evidence_gaps") or []),
                                                 str(reproduction.get("detail") or reason)])
                    if reproduction.get("status") == "unavailable":
                        parsed.update(failure_kind="reproduction_unavailable", result="failed")
                    else:
                        parsed["skip_reason"] = reason
                    break
                active_prompt_context = active_prompt_context.model_copy(update={
                    "evidence": {**active_prompt_context.evidence,
                                 "initial_analysis": parsed,
                                 "request_reproduction": {key: value for key, value in reproduction.items()
                                                          if key != "execution"}},
                })
                request = PromptRenderer().request(
                    "unified_fixer", active_prompt_context, stage="unified_fixer", workspace=str(workspace),
                    model=chosen_model, write_root=str(write_target) if write_target else None,
                    rule_ids=rule_ids, budget_tier=tier.name, role_variant=role_variant,
                )
                continue
            # 模型漏报的文件也必须自检；虚构路径不能被悄悄忽略。
            missing = [name for name in declared if not (workspace / name).is_file()
                       and name not in before]
            syntax_ok, syntax_err = _ast_check_files(workspace, changed_files, baseline_dir)
            if missing:
                syntax_ok, syntax_err = False, f"声明的变更文件不存在: {', '.join(missing)}"
            if syntax_ok or is_legacy or parsed.get("status") != "FIXED" or healing_attempts >= config.max_self_healing_turns:
                break
            healing_attempts += 1
            healing_context = active_prompt_context.model_copy(update={
                "retry_feedback": f"{active_prompt_context.retry_feedback}\n[系统自检反馈]\n{syntax_err}\n"
                                  "请在当前会话修正并输出完整 JSON；保持原验收目标和保留规则。",
                "evidence": {**active_prompt_context.evidence, "previous_result": parsed,
                             "actual_changed_files": changed_files},
            })
            request = PromptRenderer().request(
                "unified_fixer", healing_context, stage="unified_fixer", workspace=str(workspace),
                model=chosen_model, write_root=str(write_target) if write_target else None,
                rule_ids=rule_ids, budget_tier=tier.name, role_variant=role_variant,
            )
    except Exception as exc:
        logger.exception("UnifiedFixer 执行异常")
        parsed = {**parsed, "status": "MANUAL", "summary": public_exception(exc, prefix="统一分析修复执行失败"),
                  "blocked_reason": public_exception(exc, prefix="统一分析修复执行失败"), "passed": False, "result": "failed",
                  "failure_kind": "agent_invocation_failed"}
        syntax_ok, syntax_err = False, str(parsed["blocked_reason"])
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()

    changed_files = source_changes(workspace, before)
    if parsed.get("reason"):
        parsed.update(status="MANUAL", summary=str(parsed["reason"]), blocked_reason=str(parsed["reason"]))
    parsed.pop("reason", None)  # Provider 解析错误通过 blocked_reason 对外保留。
    parsed.setdefault("status", "MANUAL")
    parsed["changed_files"] = changed_files
    parsed["self_check"] = {
        "syntax_valid": syntax_ok, "syntax_error": syntax_err if not syntax_ok else "",
        "reflection_notes": "AST 自检通过；业务运行验证由独立验证器执行" if syntax_ok else syntax_err,
        "self_healing_attempts": healing_attempts,
        "declared_changed_files": declared,
        "actual_changed_files": changed_files,
        "file_list_mismatch": sorted(declared) != changed_files,
    }
    if is_legacy:
        analysis_failed = bool(changed_files or parsed.get("status") == "MANUAL")
        parsed.update(status="MANUAL" if analysis_failed else "ANALYSIS_ONLY",
                      passed=False, result="failed" if analysis_failed else "passed")
        parsed["self_check"]["reflection_notes"] = "旧框架只读分析，未执行业务回归"
    elif parsed["status"] == "FIXED":
        passed = bool(changed_files and syntax_ok and not parsed.get("scope_escalation_required"))
        parsed.update(passed=passed, result="passed" if passed else "failed")
        if not passed:
            parsed["blocked_reason"] = syntax_err or "未产生实际代码变更"
    else:
        parsed.update(passed=False, result="passed" if parsed["status"] == "ANALYSIS_ONLY" and not changed_files else "failed")
        if changed_files:
            parsed.update(status="MANUAL", blocked_reason=parsed.get("blocked_reason") or "非修复结论包含实际源码变化")
    if parsed.get("failure_kind"):
        parsed.update(passed=False, result="failed")
    usage: dict[str, Any] = {}
    for key in {key for call in calls for key in call["usage"]}:
        values = [call["usage"].get(key) for call in calls]
        if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            usage[key] = sum(values)
    meta = {**(calls[-1]["audit"] if calls else {}), "calls": calls, "call_count": len(calls),
            "total_tokens": usage.get("total_tokens"), "tool_call_count": usage.get("tool_calls", 0)}
    if reproduction:
        meta["reproduction"] = reproduction
    _persist_artifacts(workspace, parsed)
    return _build_step_result(parsed, usage, meta)


def _persist_artifacts(workspace: Path, payload: dict[str, Any]) -> None:
    """落盘 artifacts/analysis.json 和 artifacts/patch.json。"""
    try:
        artifacts_dir = workspace / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        analysis_artifact = {
            "status": payload.get("status"),
            "root_cause": payload.get("root_cause"),
            "category": payload.get("category"),
            "attribution_type": payload.get("attribution_type"),
            "facts": payload.get("facts", []),
            "source_evidence": payload.get("source_evidence", []),
            "candidate_files": payload.get("candidate_files", []),
            "proposed_edit_files": payload.get("proposed_edit_files", []),
            "acceptance_criteria": payload.get("acceptance_criteria", []),
            "evidence_gaps": payload.get("evidence_gaps", []),
            "recommended_scope": payload.get("recommended_scope"),
            "summary": payload.get("summary"),
            "analysis": payload.get("analysis"),
        }
        (artifacts_dir / "analysis.json").write_text(
            json.dumps(analysis_artifact, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        patch_artifact = {
            "status": payload.get("status"),
            "changed_files": payload.get("changed_files", []),
            "change_plan": payload.get("change_plan"),
            "self_check": payload.get("self_check", {}),
            "blocked_reason": payload.get("blocked_reason", ""),
            "scope_escalation_required": payload.get("scope_escalation_required", False),
        }
        (artifacts_dir / "patch.json").write_text(
            json.dumps(patch_artifact, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        logger.warning("交付物落盘失败: %s", exc)


def _build_step_result(
    parsed: dict[str, Any], usage: dict[str, Any], meta: dict[str, Any]
) -> dict[str, Any]:
    """组装返回字典，既满足本 Agent 契约，也对齐下游数据通道。"""
    output = dict(parsed)
    output["usage"] = usage
    output["metadata"] = meta
    output["_usage"] = usage
    output["_audit"] = meta

    result = {
        **output,
        "unified_fixer": output,
        "analysis_only": output.get("status") in {"ANALYSIS_ONLY", "MANUAL"},
        "changed_files": output.get("changed_files", []),
        "passed": bool(output.get("passed")),
        "last_result": output.get("result", "failed"),
    }
    return result


def build_graph(config: UnifiedFixerConfig | None = None) -> CompiledStateGraph:
    """构建单节点统一分析修复子图。"""
    active_config = config or UnifiedFixerConfig.load()
    builder: StateGraph = StateGraph(UnifiedFixerState)

    def _execute(state: UnifiedFixerState) -> dict[str, Any]:
        return run_unified_fixer(state, active_config)

    builder.add_node("unified_fixer", _execute)
    builder.add_edge(START, "unified_fixer")
    builder.add_edge("unified_fixer", END)
    return builder.compile()


__all__ = ["build_graph", "run_unified_fixer"]
