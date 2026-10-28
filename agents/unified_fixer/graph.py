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
from infrastructure.llm import PromptRenderer
from infrastructure.llm.context import model_name, prompt_context

logger = logging.getLogger(__name__)


def _ast_check_files(workspace_root: Path, changed_files: list[str]) -> tuple[bool, str]:
    """对模型声称修改的文件进行确定性 AST 静态语法解析校验。"""
    for rel in changed_files:
        path = (workspace_root / rel).resolve()
        if not path.is_file():
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8", errors="replace"))
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

    # 1. 检查旧框架只读红线与准入
    is_legacy = legacy_analysis_required(state) or framework == "spiders"
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

    policy = state.get("agent_policy") or {}
    chosen_model = policy.get("unified_model") or getattr(config, "model", "") or model_name(runtime)
    active_prompt_context = prompt_context(
        "unified_fixer", state,
        framework=framework, evidence=evidence,
        read_roots=read_roots,
        write_roots=tuple(str(p) for p in write_roots),
        budget={"max_turns": config.max_turns, "max_context_chars": config.max_context_chars},
        source_snapshot=source_snapshot,
        retry_feedback=str(state.get("retry_reason") or ""),
        source_project_path=str(shared_root) if shared_root else "",
        project_rules=rules_text(*([carrier_root] if carrier_root else []), *([shared_root] if shared_root else [])),
    )
    request = PromptRenderer().request(
        "unified_fixer", active_prompt_context,
        stage="unified_fixer", workspace=str(workspace), model=chosen_model,
        write_root=str(write_target) if write_target else None,
    )

    provider = create_provider(config, runner=getattr(runtime, "agent_runner", None))

    # 4. 执行单会话 LLM 推理
    try:
        raw_output = provider(request)
    except Exception as exc:
        logger.exception("UnifiedFixer 调用异常: %s", exc)
        fail_summary = f"统一分析修复 Agent 执行异常: {public_exception(exc)}"
        fail_output: dict[str, Any] = {
            "status": "MANUAL",
            "root_cause": fail_summary,
            "category": "unknown",
            "attribution_type": "pending",
            "facts": [],
            "source_evidence": [],
            "candidate_files": [],
            "proposed_edit_files": [],
            "changed_files": [],
            "acceptance_criteria": [],
            "self_check": {"syntax_valid": False, "reflection_notes": fail_summary},
            "blocked_reason": fail_summary,
            "summary": fail_summary,
            "analysis": fail_summary,
            "passed": False,
            "result": "failed",
        }
        _persist_artifacts(workspace, fail_output)
        return _build_step_result(fail_output, {}, {})

    parsed = parse_result(raw_output, "unified_fixer")
    usage = usage_of(raw_output)
    meta = audit_metadata(request, usage)

    if is_legacy:
        # 只读调用仍保留真实分析与审计，不允许模型声明代码交付。
        legacy_status = "ANALYSIS_ONLY" if parsed.get("status") in {"FIXED", "ANALYSIS_ONLY"} else parsed.get("status", "MANUAL")
        parsed.update(status=legacy_status, changed_files=[], proposed_edit_files=[], passed=False,
                      result="passed" if legacy_status == "ANALYSIS_ONLY" else "failed")
        parsed["self_check"] = {"reflection_notes": "旧框架只读分析，未执行代码修改或语法回归"}
        _persist_artifacts(workspace, parsed)
        return _build_step_result(parsed, usage, meta)

    # 5. 步骤 4 确定性 AST 静态自检与反思自愈环
    changed_files = list(parsed.get("changed_files") or [])
    syntax_ok, syntax_err = _ast_check_files(workspace, changed_files)

    if not syntax_ok and changed_files:
        # 触发就地反思自愈环 (In-situ Self-Correction)
        logger.warning("UnifiedFixer AST 校验失败，触发反思自愈: %s", syntax_err)
        healing_prompt = f"静态 AST 语法校验发现语法错误：\n{syntax_err}\n请反思该错误，就地重新修改并输出修正后的完整 JSON 结果。"
        healing_context = active_prompt_context.model_copy(update={
            "retry_feedback": f"{active_prompt_context.retry_feedback}\n[系统自检反馈]\n{healing_prompt}",
        })
        request = PromptRenderer().request(
            "unified_fixer", healing_context, stage="unified_fixer",
            workspace=str(workspace), model=chosen_model,
            write_root=str(write_target) if write_target else None,
        )
        try:
            raw_output = provider(request)
            parsed = parse_result(raw_output, "unified_fixer")
            changed_files = list(parsed.get("changed_files") or [])
            syntax_ok, syntax_err = _ast_check_files(workspace, changed_files)
        except Exception as retry_exc:
            logger.error("反思自愈执行失败: %s", retry_exc)

    self_check_info = {
        "syntax_valid": syntax_ok,
        "syntax_error": syntax_err if not syntax_ok else "",
        "reflection_notes": "AST 自检通过" if syntax_ok else f"自检未通过: {syntax_err}",
    }
    parsed["self_check"] = self_check_info

    # 6. 状态收敛与归一化
    if changed_files and syntax_ok:
        parsed["status"] = "FIXED"
        parsed["passed"] = True
        parsed["result"] = "passed"
    elif parsed.get("status") in {"ANALYSIS_ONLY", "MANUAL"}:
        parsed["changed_files"] = []
        parsed["passed"] = False
        parsed["result"] = "passed" if parsed.get("status") == "ANALYSIS_ONLY" else "failed"
    else:
        if not syntax_ok:
            parsed["passed"] = False
            parsed["result"] = "failed"
            parsed["blocked_reason"] = syntax_err

    # 7. 落盘双交付物
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

    # 下游兼容影子通道：使 review_agent 和 delivery 直接读取无须大改
    locate_shadow = {
        "status": "LOCATED" if output.get("status") in {"FIXED", "ANALYSIS_ONLY"} else output.get("status", "MANUAL"),
        "root_cause": output.get("root_cause", ""),
        "category": output.get("category", "carrier"),
        "facts": output.get("facts", []),
        "source_evidence": output.get("source_evidence", []),
        "candidate_files": output.get("candidate_files", []),
        "proposed_edit_files": output.get("proposed_edit_files", []),
        "recommended_scope": output.get("recommended_scope", "carrier"),
        "summary": output.get("summary", ""),
        "passed": output.get("status") in {"FIXED", "ANALYSIS_ONLY"},
    }
    repair_shadow = {
        "passed": bool(output.get("passed")),
        "result": output.get("result", "failed"),
        "analysis": output.get("analysis", ""),
        "summary": output.get("summary", ""),
        "changed_files": output.get("changed_files", []),
        "change_plan": output.get("change_plan"),
        "blocked_reason": output.get("blocked_reason", ""),
        "scope_escalation_required": output.get("scope_escalation_required", False),
    }

    result = {
        **output,
        "unified_fixer": output,
        "locate": locate_shadow,
        "repair": repair_shadow,
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
