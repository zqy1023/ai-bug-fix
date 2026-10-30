"""审查子图与执行编排。

由只读审查 Agent 完成三项检查，并编译审查 LangGraph 子图。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from core.contracts import AgentRequest, plan_of
from core.policy import ToolPolicy
from infrastructure.llm import PromptRenderer
from infrastructure.llm.common import audit_metadata, invoke_with_audit, parse_result, raw_result, usage_of
from infrastructure.llm.context import build_context, model_name, prompt_context
from infrastructure.llm.session import ClaudeSession, resolve_tools, run_agent_once
from infrastructure.workspace.diff import file_changes, source_changes
from infrastructure.workspace.workspace import WorkspaceManager
from agents.review.contract import (
    REVIEW_CONTRACT_VERSION,
    ReviewConfig,
    ReviewState,
    ReviewView,
    judge,
    delivery_allowed,
    result_of,
    review_checks,
    slim_review,
    review_budget,
)

# =========================================================================
# 2. 模型通道 Provider (原 provider.py)
# =========================================================================

class ReviewProvider(ABC):
    """审查 agent 的通道基类。"""

    name: str = ""

    def __init__(self, config: ReviewConfig, env: Mapping[str, str] | None = None) -> None:
        self.config = config
        self.env = dict(env or {})
        self.tools = resolve_tools(writable=False, tools=self.config.tools or None)

    @abstractmethod
    def run(self, request: AgentRequest) -> dict[str, Any]:
        """执行一次调用，返回提供方原始结果。"""

    def close(self) -> None:
        """释放通道资源。"""

    def limits(self, request: AgentRequest) -> dict[str, int]:
        return {key: min(getattr(self.config, key), request.budget.get(key, getattr(self.config, key)))
                for key in ("max_turns", "max_tool_calls", "timeout_seconds")}


class ClaudeSessionProvider(ReviewProvider):
    """常驻只读会话：按任务隔离，避免共享工作区串上下文。"""

    name = "claude_session"

    def run(self, request: AgentRequest) -> dict[str, Any]:
        from infrastructure.llm.output_schema import output_schema
        from infrastructure.llm.session_context import invoke_session, session_fingerprint
        from infrastructure.llm.runtime import drop_session, session_scope

        request = request.model_copy(update={"provider": self.name})
        ToolPolicy.validate(request)

        session_key = f"{request.workspace}\x00{request.run_id}\x00{request.task_id}"
        schema = output_schema("review")
        limits = self.limits(request)
        fingerprint = session_fingerprint(request, schema, {**self.config.to_dict(), **limits}, self.env)
        with session_scope(
            request.agent_role,
            session_key,
            lambda: ClaudeSession(
                request.workspace,
                self.env,
                request.system_prompt,
                output_schema=schema,
                tools=self.tools,
                writable=False,
                **limits,
            ),
            run_id=request.run_id,
            task_id=request.task_id,
            fingerprint=fingerprint,
        ) as session:
            result = invoke_session(request, session, lambda sent: invoke_with_audit(sent, lambda: session.invoke(
                sent.prompt,
                request.workspace,
                allowed_roots=request.allowed_read_roots,
            )))
            if result.get("reason"):
                drop_session(request.agent_role, session_key)
        return result


class ClaudeQueryProvider(ReviewProvider):
    """一次性只读查询：任务间完全隔离。"""

    name = "claude_query"

    def run(self, request: AgentRequest) -> dict[str, Any]:
        from infrastructure.llm.output_schema import output_schema
        try:
            request = request.model_copy(update={"provider": self.name})
            ToolPolicy.validate(request)
            return invoke_with_audit(request, lambda: run_agent_once(
                f"{request.system_prompt}\n\n---\n\n{request.prompt}",
                request.workspace,
                self.env,
                writable=False,
                tools=self.tools,
                **self.limits(request),
                allowed_roots=request.allowed_read_roots,
                output_schema=output_schema("review"),
            ))
        except TimeoutError:
            return {"passed": False, "reason": "AGENT_TIMEOUT"}


class ManualProvider(ReviewProvider):
    """不调用模型：按转人工审查处理。"""

    name = "manual"
    _REASON = "provider=manual，审查转人工执行"

    def run(self, request: AgentRequest) -> dict[str, Any]:
        request = request.model_copy(update={"provider": self.name})
        ToolPolicy.validate(request)
        payload = {
            "verdict": "MANUAL",
            "passed": False,
            "code_verdict": "UNKNOWN",
            "coverage": {"decision": "unknown", "reason": "人工审查模式，尚未评估本次变更覆盖",
                         "reviewed_symbols": [], "missing_checks": ["人工核对源码及验证证据"]},
            "dimensions": {
                key: {"status": "unverified", "reason": "人工审查模式，等待核对源码与执行证据", "evidence": []}
                for key in ("syntax", "business", "scope")
            },
            "environment_assessment": "人工审查尚未核对验证环境，无法确认",
            "upstream_assessment": "人工审查尚未核对官网状态，无法确认",
            "summary": self._REASON,
            "findings": [],
            "required_changes": [],
            "todo": [],
        }
        return {
            "passed": True,
            "manual": True,
            "reason": self._REASON,
            "result": json.dumps(payload, ensure_ascii=False),
        }


PROVIDERS: dict[str, type[ReviewProvider]] = {
    ClaudeSessionProvider.name: ClaudeSessionProvider,
    ClaudeQueryProvider.name: ClaudeQueryProvider,
    ManualProvider.name: ManualProvider,
}


def create_provider(
    name: str,
    config: ReviewConfig,
    env: Mapping[str, str] | None = None,
) -> ReviewProvider:
    factory = PROVIDERS.get(name)
    if factory is None:
        available = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"未注册的审查 agent provider: {name}（可用: {available}）")
    return factory(config, env)


def create_provider_from_env() -> Any:
    config = ReviewConfig.load()
    return create_provider(config.provider, config)


# =========================================================================
# 3. 原始差异证据与输出整理
# =========================================================================

def _lines(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    lines: list[str] = []
    for item in value[:8]:
        if isinstance(item, str):
            lines.append(item)
        elif isinstance(item, dict):
            lines.append(str(item.get("summary") or item.get("message") or "")[:300])
    return [item for item in lines if item]


def _evidence_block(state: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """提供基线差异原始材料，不预先替 Agent 作出检查结论。"""
    workspace = str(state.get("ai_workspace_path") or "")
    baseline = str(state.get("baseline_dir") or "")
    files = [str(item) for item in state.get("changed_files") or []]
    if not baseline:
        return "缺少只读基线；请如实记录审查限制，不得声称已完成前后对比。", {}
    changes = file_changes(baseline, workspace, files)
    return json.dumps(changes, ensure_ascii=False), {}


# =========================================================================
# 4. 审查执行主节点
# =========================================================================

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
    review_kind = "patch" if changed_files else "analysis_disposition"
    if not config.enabled:
        return ReviewView(summary="执行策略关闭独立审查，禁止交付未经审查的修改或处置",
                          verdict="MANUAL", code_verdict="UNKNOWN", review_kind=review_kind,
                          contract_error="review_disabled").model_dump()
    if state.get("baseline_dir"):
        actual = source_changes(workspace, str(state["baseline_dir"]))
        if sorted(set(changed_files)) != actual:
            raise ValueError("审查变更清单与实际源码差异不一致，必须重新执行修复交接")
    from workflow.engine.evidence_version import evidence_version
    current_version = evidence_version(workspace, changed_files, str(state.get("baseline_dir") or ""))
    from infrastructure.workspace.validation import run_validation, validation_summary
    # 无补丁时审查的是分析与处置决定，不能将未修改源码的执行伪称修复验证。
    validation_report = run_validation(dict(state)) if changed_files else {
        "evidence_version": current_version,
        "replay": {"status": "unverified", "reason": "无源码改动；本轮审查分析及不修复理由，未执行修复验证"},
        "request": {"status": "unverified", "reason": "无源码改动；未执行修复后真实请求验证"},
        "executions": [],
    }
    from infrastructure.workspace.semantics import not_found_changes
    semantic_files = not_found_changes(dict(state))
    evidence_text, _ = _evidence_block(
        {**state, "ai_workspace_path": workspace, "changed_files": changed_files}
    )
    raw_diffs = json.loads(evidence_text) if state.get("baseline_dir") else []
    added = "\n".join(line.get("text", "") for change in raw_diffs
                      for line in change.get("lines", []) if line.get("type") == "add")
    semantic_risk = bool(semantic_files) or "ErrorType." in added
    limits = review_budget(config, changed_files,
                          max(len(evidence_text), len(str(state.get("diff_summary") or ""))), semantic_risk)
    validation = validation_summary(validation_report)
    full_diff_summary = str(state.get("diff_summary") or "")
    if len(full_diff_summary) > config.max_diff_chars:
        full_diff_summary = (f"完整差异超出内联摘要上限 {config.max_diff_chars} 字符；"
                             "必须读取 evidence.baseline_diff_evidence 核对所有修改，不能仅审查摘要。")
    deterministic_context = build_context(
        dict(state["problem"]),
        changed_files,
        str(state.get("diff_summary") or ""),
        max_chars=limits["max_context_chars"],
        max_candidates=config.max_candidate_files,
    )
    from core.policy import review_read_paths
    shared_root = Path(str(state["source_project_path"])).resolve() if state.get("source_project_path") else None
    roots = tuple(str(path) for path in review_read_paths(
        Path(workspace), carrier_root, shared_root, carrier, str(state.get("baseline_dir") or "")
    ))
    from core.policy import retry_read_paths
    roots = (*roots, *(str(path) for path in retry_read_paths(Path(workspace), str(state["run_id"]), str(state["task_id"]))))
    from infrastructure.llm.rules import select_rules

    rule_ids = select_rules(
        role="review",
        framework=framework,
        problem=dict(state.get("problem") or {}),
        evidence=dict(state.get("evidence") or {}),
    )
    request_context = prompt_context(
        "review",
        state,
        framework=framework,
        evidence={
            "evidence_version": current_version,
            "baseline_diff_evidence": evidence_text,
            "deterministic_context": deterministic_context,
            "changed_files": changed_files,
            "review_mode": review_kind,
            "source_ownership": {
                "framework": framework,
                "carrier_directory": carrier_dir,
                "carrier_files": sorted(path.relative_to(Path(workspace).resolve()).as_posix()
                                        for path in carrier_root.rglob("*.py"))[:64],
                "note": "平台已确认目标目录；先定向核对此目录与 repair.source_evidence 引用，仅实际调用委托或归属冲突时再扩展搜索。",
            },
            "repair_authorization": {
                "framework": framework,
                "writable_scope": f"gathers/{carrier}" if framework == "gathers" else "无；旧源码只读",
                "legacy_dependency": bool((state.get("evidence") or {}).get("legacy_dependency")),
                "boundary": "旧依赖引用不等于当前解析委托给旧实现；沿本次输入核对，不得以引用直接拒绝 gathers 局部修复",
            },
            "repair": dict(state.get("repair") or {}),
            "repair_plan": dict((state.get("repair") or {}).get("change_plan") or {}),
            "acceptance_criteria": list((state.get("repair") or {}).get("acceptance_criteria") or []),
            "validation": validation_report,
            "repair_claims_status": "以下 repair/repair_plan/acceptance_criteria 是修复方声明，需独立核对，不作为已证明事实",
            "review_responsibility": {
                "scope": "改动文件及行为范围是否合理；逐项核对旧新条件、返回和受影响输入",
                "business": "原异常是否得到符合业务依据的正确处理，不能只确认报错消失",
                "regression": "相关正常分支、边界、请求身份及返回协议是否受影响",
                "ownership": "三项业务判断由审查 Agent 负责；平台仅提供证据和风险位置，不替代判断",
            },
            "review_focus": [{"path": name, "kind": "not_found_exit_changed",
                              "question": "核对当前分支的查无结果业务依据、旧新行为和其他业务影响；不预设通过或失败"}
                             for name in semantic_files],
        },
        read_roots=roots,
        write_roots=(),
        budget={
            **limits,
        },
        source_snapshot={
            "baseline_dir": str(state.get("baseline_dir") or ""),
            "baseline_manifest": dict(state.get("baseline_manifest") or {}),
        },
        changed_files=tuple(changed_files),
        diff_summary=full_diff_summary,
        allowed_scope=(carrier_dir,),
        stop_conditions=("证据与补丁版本不一致", "真实 diff 与声明不一致", "发现未授权范围"),
        source_project_path=str(shared_root) if shared_root else "",
    )
    request = PromptRenderer().request(
        "review",
        request_context,
        stage="review",
        workspace=workspace,
        model=str((state.get("agent_policy") or {}).get("review_model") or model_name(runtime)),
        rule_ids=rule_ids,
    )
    raw = runtime.run(request)
    raw = raw if isinstance(raw, dict) else {}
    if evidence_version(workspace, changed_files, str(state.get("baseline_dir") or "")) != current_version:
        return {
            **ReviewView(
                review_kind=review_kind,
                verdict="MANUAL",
                passed=False,
                summary="审查期间源码变化，结论无效，需重跑审查 Agent",
                contract_error="source_changed_during_review",
                required_changes=["重新执行审查 Agent"],
                evidence_version=current_version,
            ).model_dump(),
            "raw_result": raw_result(raw),
            "_usage": usage_of(raw),
            "_audit": {"blocked_by": "source_changed_during_review"},
            "_validation_report": validation_report,
        }
    raw = raw if isinstance(raw, dict) else {}
    original = raw_result(raw)
    usage = usage_of(raw)
    direct_payload = "result" not in raw and ("verdict" in raw or "findings" in raw)
    provider_failed = raw.get("passed") is False and bool(raw.get("reason")) and not direct_payload
    if provider_failed or (not original.strip() and not direct_payload):
        category = (
            str(raw.get("failure_kind") or "")
            or ("agent_timeout" if raw.get("reason") == "AGENT_TIMEOUT" else "provider_error")
        ) if provider_failed else "empty_response"
        summary = (
            "审查调用超时，未取得有效审查结论" if category == "agent_timeout"
            else "审查工具放行预算耗尽，未取得有效结论" if category == "tool_budget_exhausted"
            else "审查连续遇到权限或重复操作拒绝，已停止无效调用" if category == "tool_access_blocked"
            else "审查调用失败，未取得有效审查结论" if provider_failed
            else "审查未返回结果，未取得有效审查结论"
        )
        return {
            **ReviewView(
                review_kind=review_kind,
                verdict="MANUAL",
                passed=False,
                delivery_eligible=False,
                summary=summary,
                code_verdict="UNKNOWN",
                coverage={"decision": "unknown", "reason": summary, "reviewed_symbols": [], "missing_checks": []},
                dimensions={
                    key: {"status": "unverified", "reason": summary, "evidence": []}
                    for key in ("syntax", "business", "scope")
                },
                required_changes=["重新执行 AI 审查；若持续失败，检查模型服务调用记录"],
                evidence_version=current_version,
                checks=review_checks(state, "UNKNOWN", {}),
                contract_error=f"review_invocation_failed:{category}",
                review_notes=summary,
                next_action="重新执行 AI 审查，取得有效结论后再决定交付",
            ).model_dump(),
            "raw_result": original,
            "_usage": usage,
            "_audit": {**audit_metadata(request, usage), "failure_stage": "invocation", "error_type": category},
            "_validation_report": validation_report,
        }
    payload = parse_result(raw, "review")
    # Preserve semantic judgment independently of execution availability. Never let
    # a provider-supplied flag turn an unverified business decision into approval.
    payload["review_kind"] = review_kind
    payload["disposition_verified"] = bool(not changed_files and judge(payload).passed)
    payload["semantic_verified"] = bool(changed_files and judge(payload).valid and payload.get("code_verdict") == "PASS"
        and (payload.get("dimensions", {}).get("business") or {}).get("status") == "passed")
    # 静态源码判断不能伪装成运行验证；缺证据保留代码结论并标记业务未验证。
    if changed_files and judge(payload).valid and payload.get("code_verdict") == "PASS" and any(
        (validation.get(key) or {}).get("status") != "passed" for key in ("replay", "request")
    ):
        payload = {**payload, "verdict": "MANUAL", "passed": False,
                   "dimensions": {**payload["dimensions"], "business": {
                       "status": "unverified", "reason": "；".join(f"{key}: {(validation.get(key) or {}).get('reason') or '缺少执行证据'}"
                                           for key in ("replay", "request")
                                           if (validation.get(key) or {}).get("status") != "passed"),
                       "evidence": ["artifacts/validation.json"],
                   }}}
    slim = slim_review(payload)
    findings = _lines(slim.get("findings"))
    required = _lines(slim.get("required_changes"))
    checked = judge(payload)
    result = result_of(payload)
    verdict = "MANUAL" if not checked.valid or result == "MANUAL" else ("PASS" if checked.passed else "FAIL")
    coverage = dict(payload.get("coverage") or {}) if checked.valid else {}
    required = list(dict.fromkeys(required))
    plan = plan_of(payload)
    code_verdict = str(payload.get("code_verdict") or "UNKNOWN").upper()
    if not checked.valid or code_verdict not in {"PASS", "FAIL", "UNKNOWN"}:
        code_verdict = "UNKNOWN"
    if checked.valid and code_verdict == "FAIL":
        verdict = "FAIL"
    dimensions = dict(slim.get("dimensions") or {}) if checked.valid else {}
    checks = review_checks(state, code_verdict, coverage, dimensions)
    next_action = "根据审查 Agent 的三项检查结论决定下一步"
    view = ReviewView(
        verdict=verdict,
        passed=verdict == "PASS",
        delivery_eligible=delivery_allowed(payload),
        semantic_verified=payload["semantic_verified"],
        review_kind=review_kind,
        disposition_verified=payload["disposition_verified"],
        contract_error="" if checked.valid else f"review_protocol_invalid:{checked.reason}",
        evidence_version=current_version,
        diff_files=file_changes(str(state["baseline_dir"]), workspace, changed_files) if state.get("baseline_dir") else [],
        code_verdict=code_verdict,
        coverage=coverage,
        dimensions=dimensions,
        environment_assessment=str(slim.get("environment_assessment") or ""),
        upstream_assessment=str(slim.get("upstream_assessment") or ""),
        checks=checks,
        next_action=next_action,
        validation=validation,
        review_notes=str(slim.get("summary") or ""),
        summary=str(slim.get("summary") or checked.reason) if checked.valid else f"审查输出协议错误：{checked.reason}",
        findings=findings,
        required_changes=required,
        todo=list(slim.get("todo") or []) if checked.valid else [],
        unresolved_issues=list(slim.get("unresolved_issues") or []) if checked.valid else [],
        change_plan=plan.model_dump(mode="json") if plan is not None else {},
    )
    return {
        **view.model_dump(),
        "raw_result": original,
        "_usage": usage,
        "_audit": audit_metadata(request, usage),
        "_validation_report": validation_report,
    }


# =========================================================================
# 5. 子图编译
# =========================================================================

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


__all__ = [
    "ClaudeQueryProvider",
    "ClaudeSessionProvider",
    "ManualProvider",
    "PROVIDERS",
    "ReviewProvider",
    "build",
    "create_provider",
    "create_provider_from_env",
    "review",
]
