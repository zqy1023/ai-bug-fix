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
from infrastructure.workspace.diff import file_changes
from infrastructure.workspace.workspace import WorkspaceManager
from agents.review.contract import (
    REVIEW_CONTRACT_VERSION,
    ReviewConfig,
    ReviewState,
    ReviewView,
    judge,
    result_of,
    review_checks,
    slim_review,
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
        fingerprint = session_fingerprint(request, schema, self.config.to_dict(), self.env)
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
                max_turns=self.config.max_turns,
                max_tool_calls=self.config.max_tool_calls,
                timeout_seconds=self.config.timeout_seconds,
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
                max_turns=self.config.max_turns,
                max_tool_calls=self.config.max_tool_calls,
                timeout_seconds=self.config.timeout_seconds,
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
    from workflow.engine.evidence_version import evidence_version
    current_version = evidence_version(workspace, changed_files, str(state.get("baseline_dir") or ""))
    evidence_text, _ = _evidence_block(
        {**state, "ai_workspace_path": workspace, "changed_files": changed_files}
    )
    full_diff_summary = str(state.get("diff_summary") or "")
    deterministic_context = build_context(
        dict(state["problem"]),
        changed_files,
        str(state.get("diff_summary") or ""),
        max_chars=config.max_context_chars,
        max_candidates=config.max_candidate_files,
    )
    from core.policy import review_read_paths
    shared_root = Path(str(state["source_project_path"])).resolve() if state.get("source_project_path") else None
    roots = tuple(str(path) for path in review_read_paths(
        Path(workspace), carrier_root, shared_root, carrier, str(state.get("baseline_dir") or "")
    ))
    from core.policy import retry_read_paths
    roots = (*roots, *(str(path) for path in retry_read_paths(Path(workspace), str(state["run_id"]), str(state["task_id"]))))
    request_context = prompt_context(
        "review",
        state,
        framework=framework,
        evidence={
            "evidence_version": current_version,
            "baseline_diff_evidence": evidence_text,
            "deterministic_context": deterministic_context,
            "changed_files": changed_files,
            "repair_plan": dict((state.get("repair") or {}).get("change_plan") or {}),
            "review_responsibility": "由审查 Agent 独立检查语法、业务修复和影响范围；没有实际执行的验证必须明确说明",
        },
        read_roots=roots,
        write_roots=(),
        budget={
            "max_tool_calls": config.max_tool_calls,
            "max_turns": config.max_turns,
            "max_context_chars": config.max_context_chars,
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
        model=model_name(runtime),
    )
    raw = runtime.run(request)
    if evidence_version(workspace, changed_files, str(state.get("baseline_dir") or "")) != current_version:
        return {
            **ReviewView(
                verdict="MANUAL",
                passed=False,
                summary="审查期间源码变化，结论无效，需重跑审查 Agent",
                required_changes=["重新执行审查 Agent"],
                evidence_version=current_version,
            ).model_dump(),
            "raw_result": "",
            "_usage": {},
            "_audit": {"blocked_by": "source_changed_during_review"},
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
                verdict="MANUAL",
                passed=False,
                delivery_eligible=False,
                summary=summary,
                code_verdict="UNKNOWN",
                required_changes=["重新执行 AI 审查；若持续失败，检查模型服务调用记录"],
                evidence_version=current_version,
                checks=review_checks(state, "UNKNOWN", {}),
                next_action="重新执行 AI 审查，取得有效结论后再决定交付",
            ).model_dump(),
            "raw_result": original,
            "_usage": usage,
            "_audit": {**audit_metadata(request, usage), "failure_stage": "invocation", "error_type": category},
        }
    payload = parse_result(raw, "review")
    slim = slim_review(payload)
    findings = _lines(slim.get("findings"))
    required = _lines(slim.get("required_changes"))
    checked = judge(payload)
    result = result_of(payload)
    verdict = "MANUAL" if checked.valid and result == "MANUAL" else ("PASS" if checked.passed else "FAIL")
    coverage = dict(payload.get("coverage") or {}) if checked.valid else {}
    required = list(dict.fromkeys(required))
    plan = plan_of(payload)
    code_verdict = str(payload.get("code_verdict") or "UNKNOWN").upper()
    if code_verdict not in {"PASS", "FAIL", "UNKNOWN"}:
        code_verdict = "UNKNOWN"
    if checked.valid and code_verdict == "FAIL":
        verdict = "FAIL"
    dimensions = dict(slim.get("dimensions") or {}) if checked.valid else {}
    checks = review_checks(state, code_verdict, coverage, dimensions)
    next_action = "根据审查 Agent 的三项检查结论决定下一步"
    view = ReviewView(
        verdict=verdict,
        passed=verdict == "PASS",
        delivery_eligible=False,
        contract_error="" if checked.valid else checked.reason,
        evidence_version=current_version,
        diff_files=file_changes(str(state["baseline_dir"]), workspace, changed_files) if state.get("baseline_dir") else [],
        code_verdict=code_verdict,
        coverage=coverage,
        dimensions=dimensions,
        environment_assessment=str(slim.get("environment_assessment") or ""),
        upstream_assessment=str(slim.get("upstream_assessment") or ""),
        checks=checks,
        next_action=next_action,
        review_notes=str(slim.get("summary") or ""),
        summary=str(slim.get("summary") or checked.reason) if checked.valid else f"审查输出协议错误：{checked.reason}",
        findings=findings,
        required_changes=required,
        change_plan=plan.model_dump(mode="json") if plan is not None else {},
    )
    return {
        **view.model_dump(),
        "raw_result": original,
        "_usage": usage,
        "_audit": audit_metadata(request, usage),
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
