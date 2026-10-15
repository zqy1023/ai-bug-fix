"""从确定性工作流状态构造 Agent Prompt 上下文。"""

from __future__ import annotations

from typing import Any

from app.infrastructure.filesystem.workspace import WorkspaceManager
from app.application.contracts import AgentRole
from app.prompts.models import PromptContext


def framework_for(workspace: str, carrier: str) -> str:
    """使用与预检相同的物理执行条件判定框架。"""
    from pathlib import Path

    root = Path(workspace).resolve()
    if (root / "gathers" / WorkspaceManager.safe_carrier_name(carrier) / "main.py").is_file():
        return "gathers"
    if (root / "spiders" / WorkspaceManager.safe_carrier_name(carrier)).is_dir():
        return "spiders"
    raise ValueError(f"工作区没有可执行的 carrier 实现: {carrier}")


def prompt_context(
    role: AgentRole,
    state: dict[str, Any],
    *,
    framework: str,
    evidence: dict[str, Any],
    read_roots: tuple[str, ...],
    write_roots: tuple[str, ...],
    budget: dict[str, int],
    retry_feedback: str = "",
    source_snapshot: dict[str, Any] | None = None,
    changed_files: tuple[str, ...] = (),
    diff_summary: str = "",
    gate_results: dict[str, Any] | None = None,
    verification_results: dict[str, Any] | None = None,
    regression_results: dict[str, Any] | None = None,
    allowed_scope: tuple[str, ...] = (),
    stop_conditions: tuple[str, ...] = (),
) -> PromptContext:
    """从 ES 原始问题和图身份字段创建不可变 PromptContext。"""
    problem = state.get("problem")
    if not isinstance(problem, dict):
        raise ValueError("Agent 输入缺少原始 problem 对象")
    required = {key: problem.get(key) for key in ("service_name", "biztype", "carrier")}
    for key, value in required.items():
        if not isinstance(value, str) or not value:
            raise ValueError(f"Agent 输入的原始 problem 缺少 {key}")
    run_id = state.get("run_id")
    task_id = state.get("task_id")
    attempt = state.get("attempt")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("Agent 输入缺少 run_id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("Agent 输入缺少 task_id")
    if not isinstance(attempt, int) or attempt < 1:
        raise ValueError("Agent 输入 attempt 必须是正整数")
    return PromptContext(
        agent_role=role,
        run_id=run_id,
        task_id=task_id,
        attempt=attempt,
        service_name=required["service_name"],
        biztype=required["biztype"],
        carrier=required["carrier"],
        framework=framework,
        problem=problem,
        source_snapshot=source_snapshot or {},
        evidence=evidence,
        changed_files=changed_files,
        diff_summary=diff_summary,
        gate_results=gate_results or {},
        verification_results=verification_results or {},
        regression_results=regression_results or {},
        allowed_scope=allowed_scope,
        stop_conditions=stop_conditions,
        allowed_read_roots=read_roots,
        allowed_write_roots=write_roots,
        budget=budget,
        retry_feedback=retry_feedback,
    )


def model_name(runtime: Any) -> str:
    """从 provider 注入环境或应用配置获取实际模型名。"""
    environment = getattr(runtime, "env", None)
    model = environment.get("ANTHROPIC_MODEL", "") if isinstance(environment, dict) else ""
    if not model:
        from app.infrastructure.config import load_config

        model = load_config().claude.model
    if not model:
        raise ValueError("缺少 ANTHROPIC_MODEL，不能生成可审计的 Agent 请求")
    return model


__all__ = ["framework_for", "model_name", "prompt_context"]
