"""修复子图。

``analyze`` 完成一次模型分析/修改，``execute`` 只提交结果。两个节点都在本目录。
对外只留 analysis / summary / changed_files / result / passed。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from app.agents.context import build_context
from app.agents.repair.contract import (
    blocked_reason_of,
    change_plan_of,
    changed_files_of,
    judge,
    needs_escalation,
    slim_plan,
)
from app.agents.repair.config import RepairConfig
from app.agents.repair.models import RepairView
from app.agents.repair.provider import create_provider
from app.agents.repair.state import RepairState
from app.agents.common import audit_metadata, parse_result, raw_result, usage_of
from app.infrastructure.redaction import public_exception
from app.prompts import PromptRenderer
from app.prompts.context import model_name, prompt_context
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph


def _call(
    runtime: Any, stage: str, state: RepairState
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    """调本目录的 provider，返回 ``(业务结果, 调用统计, 审计字段)``。

    调用统计与审计属于元数据，不混进业务结果：业务结果要按契约当模型输出用，
    元数据只走状态通道。运输商目录由系统定位，不交给模型搜索。
    """
    from app.infrastructure.filesystem.workspace import WorkspaceError, WorkspaceManager

    workspace = str(state["ai_workspace_path"])
    task = str(state["task_id"])
    carrier = str(state["carrier"])
    locate = dict(state.get("locate") or {})
    if locate.get("status") != "LOCATED":
        return {"passed": False, "reason": "缺少 locate_agent 的 LOCATED 结论", "changed_files": []}, {}, {}, ""
    try:
        evidence = dict(state["evidence"])
        if evidence.get("legacy_only") or evidence.get("framework") != "gathers":
            raise WorkspaceError("纯旧框架任务不进入 repair_agent")
        carrier_dir = str(evidence["carrier_dir"])
        expected = f"gathers/{WorkspaceManager.safe_carrier_name(carrier)}"
        if carrier_dir != expected or not (Path(workspace) / carrier_dir / "main.py").is_file():
            raise WorkspaceError(f"evidence 未指向唯一新框架入口: {carrier_dir}")
    except (WorkspaceError, KeyError) as error:
        return {
            "passed": False,
            "reason": public_exception(error, prefix="修复工作区校验失败"),
            "changed_files": [],
        }, {}, {}, ""
    # 读写范围均绑定到已经验证的新框架 carrier 目录。
    readable = [carrier_dir, *WorkspaceManager.readable_rule_paths(workspace)]
    baseline_manifest = dict(state.get("baseline_manifest") or {})
    baseline_dir = str(state.get("baseline_dir") or "")
    if not baseline_manifest or not baseline_dir:
        return {"passed": False, "reason": "缺少 prepare_source_snapshot 基线", "changed_files": []}, {}, {}, ""
    evidence = dict(state.get("evidence") or {})
    config = getattr(runtime, "config", None)
    config = config if isinstance(config, RepairConfig) else RepairConfig.load()
    deterministic_analysis = build_context(
        dict(state.get("problem") or {}),
        list(evidence.get("candidates") or []),
        str(state.get("retry_reason") or ""),
        max_chars=config.max_context_chars,
        max_candidates=config.max_candidate_files,
        symbols=evidence.get("symbols") or {},
        category=str(evidence.get("category") or ""),
    )
    read_roots = tuple(str(Path(workspace) / item) for item in readable)
    write_root = str((Path(workspace) / carrier_dir).resolve())
    prompt_input = prompt_context(
        "repair",
        {**state, "run_id": str(state["run_id"]), "task_id": task, "carrier": carrier},
        framework=str(evidence["framework"]),
        evidence={
            "prepared_source_evidence": evidence,
            "locate": locate,
            "deterministic_analysis": deterministic_analysis,
        },
        read_roots=read_roots,
        write_roots=(write_root,),
        budget={"max_tool_calls": config.max_tool_calls, "max_turns": config.max_turns},
        retry_feedback=str(state.get("retry_reason") or ""),
        source_snapshot={
            "baseline_dir": baseline_dir,
            "baseline_manifest": baseline_manifest,
        },
        changed_files=tuple(str(item) for item in state.get("changed_files") or []),
        allowed_scope=(carrier_dir,),
        stop_conditions=("Locate 未确认根因", "共享层根因", "目标 carrier 外修改"),
    )
    request = PromptRenderer().request(
        "repair",
        prompt_input,
        stage=stage,
        workspace=workspace,
        model=model_name(runtime),
        write_root=write_root,
    )
    raw = runtime.run(request)
    raw = raw if isinstance(raw, dict) else {}
    payload = parse_result(raw, "repair")
    usage = usage_of(raw)
    return payload, usage, audit_metadata(request, usage), raw_result(raw)


def analyze(state: RepairState, runtime: Any) -> dict[str, Any]:
    """生成方案。

    ``passed=false`` 时仍保留结构化 ``change_plan`` 并给出 ``blocked_reason``：
    根因在共享层的任务由此转分析报告，而不是重试到耗尽后以失败收场。
    """
    payload, usage, audit, original = _call(runtime, "analyze", state)
    plan = slim_plan(payload)
    verdict = judge(payload)
    changed = changed_files_of(plan) if verdict.passed else []
    syntax_error = _run_syntax_check(str(state.get("ai_workspace_path") or ""), changed)
    if syntax_error:
        verdict = type(verdict)(passed=False, valid=verdict.valid, reason=syntax_error)
    change_plan = change_plan_of(payload)
    return {
        "analysis": str(plan.get("analysis") or ""),
        "summary": str(plan.get("summary") or verdict.reason),
        "result": "passed" if verdict.passed else "failed",
        "passed": verdict.passed,
        "changed_files": changed if verdict.passed else [],
        "change_plan": change_plan,
        "blocked_reason": blocked_reason_of(payload, verdict),
        "scope_escalation_required": needs_escalation(payload, passed=verdict.passed),
        "baseline_manifest": dict(state.get("baseline_manifest") or {}),
        "baseline_dir": str(state.get("baseline_dir") or ""),
        "raw_result": original,
        "_usage": usage,
        "_audit": audit,
    }


def _run_syntax_check(workspace: str, changed_files: list[str]) -> str:
    """Deterministic equivalent of the agent-side RunSyntaxCheck tool."""
    root = Path(workspace).resolve()
    for relative in changed_files:
        target = (root / str(relative)).resolve(strict=False)
        try:
            target.relative_to(root)
            # The deterministic gate owns existence/diff validation. This
            # helper only checks syntax for files the model actually created.
            if not target.is_file():
                continue
            # 只对 Python 源码做语法检查：先判后缀再读，避免把模型合法修改的
            # 非 .py 文件（JSON/CSV/二进制）当成语法失败而否决整轮修复。
            if target.suffix.casefold() != ".py":
                continue
            source = target.read_text(encoding="utf-8")
            ast.parse(source, filename=str(target))
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError, RecursionError) as error:
            return public_exception(error, prefix=f"RunSyntaxCheck 失败: {relative}")
    return ""


def execute(state: RepairState, runtime: Any) -> dict[str, Any]:
    """提交首节点结果，方案与阻塞原因原样带出。"""
    verdict = judge(
        {
            "passed": state.get("result") == "passed",
            "analysis": str(state.get("analysis") or ""),
            "summary": str(state.get("summary") or ""),
            "changed_files": list(state.get("changed_files") or []),
        }
    )
    view = RepairView(
        analysis=str(state.get("analysis") or ""),
        summary=str(state.get("summary") or verdict.reason),
        result="passed" if verdict.passed else "failed",
        passed=verdict.passed,
        changed_files=list(state.get("changed_files") or []) if verdict.passed else [],
        change_plan=dict(state.get("change_plan") or {}),
        blocked_reason=str(state.get("blocked_reason") or ""),
        scope_escalation_required=bool(state.get("scope_escalation_required")),
    )
    return {
        **view.model_dump(),
        "changed_files": list(state.get("changed_files") or []) if verdict.passed else [],
        "baseline_manifest": dict(state.get("baseline_manifest") or {}),
        "baseline_dir": str(state.get("baseline_dir") or ""),
        "_audit": dict(state.get("_audit") or {}),
    }


def build(runtime: Any | None = None) -> CompiledStateGraph:
    """编译单节点修复子图，单次调用完成分析和必要修改。"""
    resolved = runtime or create_provider_from_env()

    def repair_agent(state: RepairState) -> dict[str, Any]:
        return analyze(state, resolved)

    graph: StateGraph = StateGraph(RepairState)
    graph.add_node("repair_agent", repair_agent)
    graph.add_edge(START, "repair_agent")
    graph.add_edge("repair_agent", END)
    return graph.compile()


def create_provider_from_env() -> Any:
    """按修复目录自己的配置创建 provider。"""
    from app.agents.repair.config import RepairConfig

    config = RepairConfig.load()
    return create_provider(config.provider, config)


__all__ = ["analyze", "build", "execute"]
