"""只读根因定位子图。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.agents.common import audit_metadata, parse_result, raw_result, usage_of
from app.agents.locate.config import LocateConfig
from app.agents.locate.models import LocateInput, LocateOutput
from app.agents.locate.provider import create_provider
from app.agents.locate.state import LocateState
from app.infrastructure.filesystem.workspace import WorkspaceManager
from app.prompts import PromptRenderer
from app.prompts.context import model_name, prompt_context
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

_SHARED_READ_DIRECTORIES = ("framework", "gathers_base", "gather_providers", "common")


def _relative_file(root: Path, value: Any, *, must_exist: bool = True) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("预检文件路径必须是非空字符串")
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"预检文件路径不是安全的工作区相对路径: {value}")
    unresolved = root / candidate
    if any(
        root.joinpath(*candidate.parts[:index]).is_symlink()
        for index in range(1, len(candidate.parts) + 1)
    ):
        raise ValueError(f"预检文件路径包含符号链接: {value}")
    target = (root / candidate).resolve(strict=False)
    try:
        relative = target.relative_to(root).as_posix()
    except ValueError as error:
        raise ValueError(f"预检文件路径越出工作区: {value}") from error
    if must_exist and (not target.is_file() or unresolved.is_symlink()):
        raise ValueError(f"预检候选文件不存在或是符号链接: {value}")
    return relative


def _read_roots(root: Path, carrier_dir: str, rules: list[str]) -> tuple[str, ...]:
    paths = [root / carrier_dir]
    paths.extend(root / name for name in _SHARED_READ_DIRECTORIES if (root / name).is_dir())
    plugin_registry = root / "plugins.py"
    if plugin_registry.is_file():
        paths.append(plugin_registry)
    paths.extend(root / item for item in rules)
    return tuple(str(path.resolve()) for path in paths)


def locate(state: LocateState, runtime: Any) -> dict[str, Any]:
    """用原始异常、预检候选和真实源码证据定位根因。"""
    input_values = {key: state[key] for key in LocateInput.model_fields if key in state}
    incoming = LocateInput.model_validate(input_values)
    root = Path(incoming.source_project_path).resolve()
    if not root.is_dir():
        raise ValueError(f"定位工作区不存在: {root}")
    evidence = incoming.evidence
    carrier_dir = str(evidence.get("carrier_dir") or "")
    framework = evidence.get("framework")
    expected_dir = (
        Path(str(framework)) / WorkspaceManager.safe_carrier_name(incoming.carrier)
        if framework in {"gathers", "spiders"}
        else None
    )
    carrier_root = (root / expected_dir).resolve() if expected_dir is not None else root
    if (
        expected_dir is None
        or not carrier_root.is_dir()
        or carrier_dir != expected_dir.as_posix()
        or (framework == "gathers" and not (carrier_root / "main.py").is_file())
        or (
            framework == "spiders"
            and (root / "gathers" / WorkspaceManager.safe_carrier_name(incoming.carrier) / "main.py").is_file()
        )
    ):
        return _manual("源码快照框架与 prepare_evidence 结果不一致，停止并转人工")

    config = getattr(runtime, "config", None)
    config = config if isinstance(config, LocateConfig) else LocateConfig.load()
    candidates = evidence.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return _manual("预检没有候选源码文件，缺少定位证据")
    if len(candidates) > config.max_candidate_files:
        return _manual("预检候选数量超过 locate.max_candidate_files，要求确定性预检收敛")
    verified_candidates = [_relative_file(root, item) for item in candidates]
    for relative in verified_candidates:
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(carrier_root):
            return _manual(f"预检候选超出目标 carrier 范围: {relative}")

    serialized_problem = json.dumps(incoming.problem, ensure_ascii=False)
    if len(serialized_problem) > config.max_context_chars:
        return _manual("原始问题超过 locate.max_context_chars，拒绝截断真实输入")
    rules = WorkspaceManager.readable_rule_paths(root)
    roots = _read_roots(root, carrier_dir, rules)
    prepared_evidence = {
        key: evidence.get(key)
        for key in ("carrier_dir", "candidates", "symbols", "category", "extracted", "legacy_only", "legacy_reason")
        if key in evidence
    }
    historical_knowledge = evidence.get("knowledge_cases", [])
    context = prompt_context(
        "locate",
        incoming.model_dump(),
        framework=str(framework),
        evidence={
            "prepared_source_evidence": prepared_evidence,
            "historical_knowledge_cases": historical_knowledge,
        },
        read_roots=roots,
        write_roots=(),
        budget={
            "max_turns": config.max_turns,
            "max_tool_calls": config.max_tool_calls,
            "max_context_chars": config.max_context_chars,
            "max_candidate_files": config.max_candidate_files,
        },
        source_snapshot=incoming.source_snapshot,
        allowed_scope=(carrier_dir,),
        stop_conditions=("证据不足", "任务范围外", "源码快照与当前入口不一致"),
    )
    request = PromptRenderer().request(
        "locate",
        context,
        stage="locate",
        workspace=str(root),
        model=model_name(runtime),
    )
    raw = runtime.run(request)
    raw = raw if isinstance(raw, dict) else {}
    payload = parse_result(raw, "locate")
    original = raw_result(raw)
    usage = usage_of(raw)
    try:
        output = LocateOutput.model_validate(payload)
        _validate_output_paths(root, roots, carrier_root, output)
    except (ValueError, Exception) as error:
        output = LocateOutput(
            status="MANUAL",
            root_cause="",
            category="unknown",
            facts=[],
            source_evidence=[],
            candidate_files=[],
            evidence_gaps=[str(error)],
            recommended_scope="manual",
            summary="定位输出不满足证据或路径契约，停止并转人工",
        )
    return {
        **output.model_dump(),
        "passed": output.status == "LOCATED",
        "raw_result": original,
        "_usage": usage,
        "_audit": audit_metadata(request, usage),
    }


def _validate_output_paths(
    root: Path,
    read_roots: tuple[str, ...],
    carrier_root: Path,
    output: LocateOutput,
) -> None:
    allowed = tuple(Path(item).resolve() for item in read_roots)
    for value in output.candidate_files:
        relative = _relative_file(root, value)
        target = (root / relative).resolve()
        if not target.is_relative_to(carrier_root):
            raise ValueError(f"模型输出候选文件超出目标 carrier: {value}")
    for item in output.source_evidence:
        relative = _relative_file(root, item.path)
        target = (root / relative).resolve()
        if not any(target == base or target.is_relative_to(base) for base in allowed):
            raise ValueError(f"模型输出源码证据超出只读范围: {item.path}")


def _manual(reason: str) -> dict[str, Any]:
    return {
        "status": "MANUAL",
        "root_cause": "",
        "category": "unknown",
        "facts": [],
        "source_evidence": [],
        "candidate_files": [],
        "evidence_gaps": [reason],
        "recommended_scope": "manual",
        "summary": reason,
        "passed": False,
        "_usage": {},
        "_audit": {},
    }


def build(runtime: Any | None = None) -> CompiledStateGraph:
    """编译可单独运行的定位子图。"""
    resolved = runtime or create_provider_from_env()

    def node(state: LocateState) -> dict[str, Any]:
        return locate(state, resolved)

    graph: StateGraph = StateGraph(LocateState)
    graph.add_node("locate", node)
    graph.add_edge(START, "locate")
    graph.add_edge("locate", END)
    return graph.compile()


def create_provider_from_env() -> Any:
    config = LocateConfig.load()
    from app.infrastructure.config import load_config

    return create_provider(config.provider, config, load_config().agent_env())


__all__ = ["build", "locate"]
