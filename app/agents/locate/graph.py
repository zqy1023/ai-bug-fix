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
from app.agents.policy import rules_text
from app.infrastructure.filesystem.workspace import WorkspaceManager
from app.infrastructure.redaction import redact_output
from app.prompts import PromptRenderer
from app.prompts.context import model_name, prompt_context
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph


def _evidence_target(
    root: Path, value: Any, shared_root: Path | None
) -> Path:
    """把模型给出的证据路径解析到工作区或共享只读树，并拒绝越界写法。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("源码证据路径必须是非空字符串")
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"源码证据路径不是安全相对路径: {value}")
    for base in (root, shared_root):
        if base is None:
            continue
        # 和预检使用相同的存在性、符号链接和目录边界约束。
        if (base / candidate).exists() or (base / candidate).is_symlink():
            relative = _relative_file(base, value)
            return (base / relative).resolve()
    raise ValueError(f"源码证据文件不存在: {value}")


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


def _read_roots(
    root: Path,
    carrier_dir: str,
    rules: list[str],
    legacy_carrier_dir: str = "",
    shared_root: Path | None = None,
) -> tuple[str, ...]:
    from app.agents.policy import carrier_read_paths
    return tuple(str(path) for path in carrier_read_paths(root, root / carrier_dir, shared_root, Path(carrier_dir).name))


def _snapshot_prompt_context(
    source_snapshot: dict[str, Any], framework: str, shared_root: Path | None = None
) -> dict[str, Any]:
    """给定位 Prompt 提供最小快照元数据，不暴露完整哈希 manifest。"""
    manifest = source_snapshot.get("baseline_manifest")
    manifest_count = len(manifest) if isinstance(manifest, dict) else 0
    try:
        file_count = int(source_snapshot.get("file_count") or manifest_count)
    except (TypeError, ValueError):
        file_count = manifest_count
    context: dict[str, Any] = {"framework": framework, "file_count": max(0, file_count)}
    if shared_root is not None and shared_root.is_dir():
        context["shared_project_path"] = str(shared_root)
    return context


def _locate_contract_summary(error: BaseException) -> str:
    """将契约失败的真实原因写入面向人工的摘要。"""
    detail = redact_output(" ".join(str(error).split())) or type(error).__name__
    return f"定位输出契约校验失败：{detail[:500]}；停止自动修复并转人工。"


def locate(state: LocateState, runtime: Any) -> dict[str, Any]:
    """用原始异常、预检候选和真实源码证据定位根因。"""
    input_values = {key: state[key] for key in LocateInput.model_fields if key in state}
    incoming = LocateInput.model_validate(input_values)
    root = Path(incoming.workspace_path).resolve()
    if not root.is_dir():
        raise ValueError(f"定位工作区不存在: {root}")
    evidence = incoming.evidence
    carrier_dir = str(evidence.get("carrier_dir") or "")
    framework = evidence.get("framework")
    shared_root = (
        Path(incoming.source_project_path).resolve()
        if incoming.source_project_path
        else None
    )
    if shared_root is not None and not shared_root.is_dir():
        shared_root = None
    expected_path = (
        WorkspaceManager.find_carrier_dir(root, str(framework), incoming.carrier)
        if framework in {"gathers", "spiders"}
        else None
    )
    expected_dir = (
        expected_path.relative_to(root).as_posix()
        if expected_path is not None
        else None
    )
    carrier_root = expected_path if expected_path is not None else root
    gathers_entry = WorkspaceManager.find_carrier_dir(root, "gathers", incoming.carrier)
    if (
        expected_dir is None
        or not carrier_root.is_dir()
        or carrier_dir != expected_dir
        or (framework == "gathers" and not (carrier_root / "main.py").is_file())
        or (
            framework == "spiders"
            and gathers_entry is not None
            and (gathers_entry / "main.py").is_file()
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
    try:
        verified_candidates = [_relative_file(root, item) for item in candidates]
    except ValueError as error:
        # 与其它预检不一致场景保持同一降级策略：转人工，而不是让节点崩溃。
        return _manual(f"预检候选路径非法: {error}")
    for relative in verified_candidates:
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(carrier_root):
            return _manual(f"预检候选超出目标 carrier 范围: {relative}")

    serialized_problem = json.dumps(incoming.problem, ensure_ascii=False)
    if len(serialized_problem) > config.max_context_chars:
        return _manual(
            f"原始问题长度 {len(serialized_problem)} 字符，超过 locate.max_context_chars="
            f"{config.max_context_chars}；为避免丢失真实异常上下文，本次未截断输入，也未调用模型。"
        )
    rules = WorkspaceManager.readable_rule_paths(root)
    legacy_root = WorkspaceManager.find_carrier_dir(root, "spiders", incoming.carrier)
    legacy_carrier_dir = (
        legacy_root.relative_to(root).as_posix() if legacy_root is not None else ""
    )
    roots = _read_roots(root, carrier_dir, rules, legacy_carrier_dir, shared_root)
    from app.agents.policy import retry_read_paths
    roots = (*roots, *(str(path) for path in retry_read_paths(root, incoming.run_id, incoming.task_id)))
    project_rules = rules_text(root, *([shared_root] if shared_root else []))
    prepared_evidence = {
        key: evidence.get(key)
        for key in ("carrier_dir", "candidates", "symbols", "category", "extracted", "legacy_only", "legacy_dependency", "legacy_reason")
        if key in evidence
    }
    context = prompt_context(
        "locate",
        incoming.model_dump(),
        framework=str(framework),
        evidence={
            "prepared_source_evidence": prepared_evidence,
        },
        read_roots=roots,
        write_roots=(),
        budget={
            "max_turns": config.max_turns,
            "max_tool_calls": config.max_tool_calls,
            "max_context_chars": config.max_context_chars,
            "max_candidate_files": config.max_candidate_files,
        },
        # manifest 是完整性校验清单，可能包含大量文件哈希；定位只需要框架和规模，
        # 候选文件路径已由 prepared_source_evidence 单独提供，不应把整份 manifest 塞进 Prompt。
        source_snapshot=_snapshot_prompt_context(
            incoming.source_snapshot, str(framework), shared_root
        ),
        allowed_scope=(carrier_dir,),
        stop_conditions=("证据不足", "任务范围外", "源码快照与当前入口不一致"),
        source_project_path=str(shared_root) if shared_root is not None else "",
        project_rules=project_rules,
    )
    request = PromptRenderer().request(
        "locate",
        context,
        stage="locate",
        workspace=str(root),
        model=model_name(runtime),
    )
    # max_context_chars 限制序列化后的原始异常；渲染后的 Prompt 还包含身份、候选源码证据、
    # 快照摘要和输出契约。预留 30k 字符结构开销，避免 50k 原始异常被重复包装后误判超限。
    prompt_limit = config.max_context_chars + 30000
    if len(request.prompt) > prompt_limit:
        return _manual(
            f"定位用户上下文长度 {len(request.prompt)} 字符，超过允许值 {prompt_limit} "
            f"(locate.max_context_chars={config.max_context_chars} + 30000 字符结构开销)；"
            "未截断原始异常，也未调用模型。请压缩重复证据后重试。"
        )
    raw = runtime.run(request)
    raw = raw if isinstance(raw, dict) else {}
    payload = parse_result(raw, "locate")
    original = raw_result(raw)
    usage = usage_of(raw)
    output: LocateOutput | None = None
    try:
        output = LocateOutput.model_validate(payload)
        _validate_output_paths(root, roots, carrier_root, output, shared_root)
    except Exception as error:
        # 校验失败必须停止自动修复，但不能把 Agent 已提供的诊断证据全部清空。
        # 候选文件只保留目标 carrier 内的路径；共享层文件仍可留在 source_evidence
        #（前提是它们位于明确允许的只读根目录），绝不能借此扩大写入范围。
        if output is None:
            try:
                fallback = _manual_payload(payload)
                fallback["status"] = "MANUAL"
                fallback["recommended_scope"] = "manual"
                fallback["passed"] = False
                output = LocateOutput.model_validate(fallback)
            except Exception:
                output = LocateOutput(
                    status="MANUAL",
                    root_cause="",
                    category="unknown",
                    facts=[],
                    source_evidence=[],
                    candidate_files=[],
                    evidence_gaps=[],
                    recommended_scope="manual",
                    summary="",
                )
        output.status = "MANUAL"
        output.recommended_scope = "manual"
        output.passed = False
        output.candidate_files = _safe_carrier_candidates(
            root, carrier_root, output.candidate_files
        )
        output.source_evidence = _safe_source_evidence(
            root, roots, output.source_evidence, shared_root
        )
        detail = redact_output(" ".join(str(error).split())) or type(error).__name__
        if detail not in output.evidence_gaps:
            output.evidence_gaps.append(detail[:500])
        contract_summary = _locate_contract_summary(error)
        if contract_summary not in output.summary:
            output.summary = f"{output.summary.strip()}；{contract_summary}".strip("；")
    return {
        **output.model_dump(),
        "passed": output.status == "LOCATED",
        "raw_result": original,
        "_usage": usage,
        "_audit": audit_metadata(request, usage),
    }


def _manual_payload(payload: Any) -> dict[str, Any]:
    """逐字段保留合法诊断，原始严格契约失败仍由调用方记录并转人工。"""
    from pydantic import TypeAdapter, ValidationError

    recovered: dict[str, Any] = {"category": "unknown"}
    if not isinstance(payload, dict):
        return recovered
    for name, field in LocateOutput.model_fields.items():
        if name not in payload:
            continue
        try:
            recovered[name] = TypeAdapter(field.annotation).validate_python(payload[name])
        except (ValidationError, TypeError, ValueError):
            if name == "source_evidence" and isinstance(payload[name], list):
                from app.agents.locate.models import SourceEvidence

                items = []
                for item in payload[name]:
                    try:
                        items.append(SourceEvidence.model_validate(item))
                    except (ValidationError, TypeError, ValueError):
                        continue
                recovered[name] = items
    return recovered


def _safe_carrier_candidates(
    root: Path, carrier_root: Path, values: list[str]
) -> list[str]:
    """只保留目标 carrier 内真实存在且路径安全的候选文件。"""
    accepted: list[str] = []
    for value in values:
        try:
            relative = _relative_file(root, value)
            if (root / relative).resolve().is_relative_to(carrier_root):
                accepted.append(relative)
        except (OSError, ValueError):
            continue
    return list(dict.fromkeys(accepted))


def _safe_source_evidence(
    root: Path,
    read_roots: tuple[str, ...],
    items: list[Any],
    shared_root: Path | None = None,
) -> list[Any]:
    """只保留获准只读根目录内的真实源码证据。"""
    allowed = tuple(Path(item).resolve() for item in read_roots)
    accepted: list[Any] = []
    for item in items:
        try:
            target = _evidence_target(root, item.path, shared_root)
            if any(target == base or target.is_relative_to(base) for base in allowed):
                accepted.append(item)
        except (AttributeError, OSError, ValueError):
            continue
    return accepted


def _validate_output_paths(
    root: Path,
    read_roots: tuple[str, ...],
    carrier_root: Path,
    output: LocateOutput,
    shared_root: Path | None = None,
) -> None:
    allowed = tuple(Path(item).resolve() for item in read_roots)
    for value in output.candidate_files:
        target = _evidence_target(root, value, shared_root)
        if target.is_relative_to(carrier_root):
            continue
        # candidate_files 是调查文件，不是修改声明；不能据此升级写入范围。
        if not any(target == base or target.is_relative_to(base) for base in allowed):
            raise ValueError(f"共享层候选文件超出只读证据范围: {value}")
    for item in output.source_evidence:
        target = _evidence_target(root, item.path, shared_root)
        if not any(target == base or target.is_relative_to(base) for base in allowed):
            raise ValueError(f"模型输出源码证据超出只读范围: {item.path}")
    from app.agents.policy import carrier_write_paths
    writable = carrier_write_paths(root, carrier_root.name)
    for value in output.proposed_edit_files:
        relative = _relative_file(root, value, must_exist=False)
        target = (root / relative).resolve()
        if not any(target.is_relative_to(base) for base in writable):
            output.recommended_scope = "project"


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
