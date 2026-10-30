"""Claude 智能体受限适配器。

工具集和读写权限由调用方（agent 配置）注入：只读 agent 即使被误配置了
``Write``/``Edit`` 也会在 ``resolve_tools`` 中被剔除。所有工具调用都经过 SDK
``PreToolUse`` hook 的路径检查，避免提示词约束成为唯一安全边界。
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from config.settings import parse_bool, settings
from infrastructure.llm.tool_budget import ToolBudget

_ALL_TOOLS = (
    "Read",
    "Glob",
    "Grep",
    "Write",
    "Edit",
    "Bash",
    "Task",
    # "WebFetch",
    # "WebSearch",
    "Skill",
    "NotebookEdit",
)
_WRITE_TOOLS = frozenset({"Write", "Edit", "NotebookEdit"})


class ClaudeAgentUnavailable(RuntimeError):
    pass


def validate_process_runtime() -> None:
    """Fail before spawning the SDK when the imported runtime is incompatible."""
    import inspect
    import anyio
    from importlib.metadata import version

    if "user" not in inspect.signature(anyio.open_process).parameters:
        raise ClaudeAgentUnavailable(
            f"模型启动依赖不兼容：Python={sys.executable}，AnyIO={version('anyio')}，"
            "open_process 缺少 user 参数；请使用项目 .venv 并安装 requirements.txt 中的依赖"
        )


_MAX_EVENT_TEXT = 2000
_MAX_EVENT_VALUE = 200
_MAX_EVENTS = 200
_TOOL_INPUT_KEYS = (
    "file_path",
    "notebook_path",
    "pattern",
    "path",
    "description",
    "url",
    "query",
    "skill",
    "glob",
)


def _tokens(usage: Any) -> dict[str, Any]:
    """从 SDK 结果里取 token；缺失保持未知，不冒充零消耗。"""
    if not isinstance(usage, dict):
        return {"input_tokens": None, "output_tokens": None, "total_tokens": None, "token_total_scope": "unknown"}
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    input_tokens = input_tokens if isinstance(input_tokens, int) else None
    output_tokens = output_tokens if isinstance(output_tokens, int) else None
    total = usage.get("total_tokens")
    scope = "sdk_reported" if isinstance(total, int) else "input_plus_output_excludes_cache"
    if not isinstance(total, int):
        total = input_tokens + output_tokens if input_tokens is not None and output_tokens is not None else None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total,
        "token_total_scope": scope if total is not None else "unknown",
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
    }


def _sdk_receipt(result: Any) -> dict[str, Any]:
    """保留 SDK 实际回执，配置模型与实际用量来源分别呈现。"""
    if result is None:
        return {}
    return {name: getattr(result, name, None) for name in
            ("session_id", "subtype", "is_error", "num_turns", "model_usage", "stop_reason", "api_error_status")}


def _decision_event(name: str, values: dict[str, Any], tool_id: str | None,
                    decision: str, reason: str, started_at: float) -> dict[str, Any]:
    """在真实工具开始之前记录身份、权限和读取文件版本。"""
    event: dict[str, Any] = {"type": "tool_decision", "tool_use_id": tool_id,
                             "tool": name, "input": dict(values), "decision": decision, "reason": reason,
                             "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 2)}
    path = Path(str(values.get("file_path") or ""))
    if name == "Read" and decision == "allow" and path.is_file():
        import hashlib
        try:
            event["source_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            event["source_hash_error"] = str(error)
    return event


def _clip(value: object, limit: int) -> str:
    """压成单行并截断，同时保留原始长度，便于判断被截掉了多少。"""
    text = " ".join(str(value).split())
    return text if len(text) <= limit else f"{text[:limit]}…（共 {len(text)} 字符）"


def _text_of(content: Any) -> str:
    """从工具结果的 content 里取文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(str(block["text"]))
        return "\n".join(parts)
    return "" if content is None else str(content)


def _tool_input_summary(tool_input: Any) -> dict[str, Any]:
    """完整保留公开工具入参，包含读取区间和实际编辑内容。"""
    return dict(tool_input) if isinstance(tool_input, dict) else {}


def _public_events(message: Any) -> list[dict[str, Any]]:
    """把 SDK 消息压成"有内容的事件"，空消息直接丢弃。

    旧实现只取 text block：工具调用、系统消息、思考块全部变成
    ``{"type": ..., "text": ""}``，事件记录里一半是空条目。现在按内容拆分：
    助手文本、思考、工具调用（工具名 + 定位参数）、工具结果（含错误标记）。
    思考文本按审计要求保留；凭据仍由 provider 脱敏，不进入事件。
    """
    events: list[dict[str, Any]] = []
    content = getattr(message, "content", None)
    if isinstance(content, str):
        if content.strip():
            events.append({"type": "assistant", "text": content})
        return events
    if not isinstance(content, list):
        return events
    for block in content:
        tool_name = getattr(block, "name", None)
        tool_input = getattr(block, "input", None)
        if isinstance(tool_name, str) and tool_input is not None:
            events.append(
                {"type": "tool_use", "tool_use_id": getattr(block, "id", None), "tool": tool_name, "input": _tool_input_summary(tool_input)}
            )
            continue
        if hasattr(block, "tool_use_id"):
            events.append(
                {
                    "type": "tool_result",
                    "tool_use_id": getattr(block, "tool_use_id", None),
                    "is_error": bool(getattr(block, "is_error", False)),
                    "text": _text_of(getattr(block, "content", "")),
                }
            )
            continue
        thinking = getattr(block, "thinking", None)
        if isinstance(thinking, str) and thinking.strip():
            events.append({"type": "thinking", "text": thinking})
            continue
        if getattr(block, "type", None) == "redacted_thinking":
            events.append({"type": "thinking", "text": "", "redacted": True})
            continue
        text = getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            events.append({"type": "assistant", "text": text})
    return events


def _collect_events(
    events: list[dict[str, Any]], message: Any, started_at: float | None = None
) -> None:
    """记录公开 SDK 事件及其相对调用开始的时间。"""
    elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2) if started_at is not None else None
    for event in _public_events(message):
        if elapsed_ms is not None:
            event["elapsed_ms"] = elapsed_ms
        events.append(event)


def _resolve_tool_path(raw: object, root: Path) -> Path | None:
    """将 SDK 工具输入中的路径解析到工作区坐标系。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve(strict=False)


def _path_is_under(root: Path, candidate: Path) -> bool:
    """判断路径是否位于允许根目录内。"""
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _tool_paths(tool_name: str, tool_input: dict[str, Any], root: Path) -> list[Path]:
    """提取文件工具可能使用的所有路径字段。

    ``Glob`` 的 ``pattern`` 是**匹配式**，不是路径：``*`` 或 ``**/*.py`` 这类值
    解析成 ``<root>/*`` 会落在 carrier 之外，从而把一个完全合法的
    ``path=gathers/<carrier>`` 调用误判成越界。

    所以对 ``pattern`` 分两种情况：
    - 不含通配符：它就是一个普通路径（如 ``gathers/UPS/main.py``），直接采用；
    - 含通配符：取通配符之前的**目录前缀**（``gathers/UPS/**/*.py`` →
      ``gathers/UPS``），用它做范围校验。模型经常只给 pattern 不给 path
      （``Glob(pattern="gathers/UPS/**/*.py")``），若不提取前缀会因"缺少路径"
      被拒，模型只能退回逐文件 Read，白白浪费轮次。
    """
    paths: list[Path] = []
    for key in ("file_path", "notebook_path", "path"):
        path = _resolve_tool_path(tool_input.get(key), root)
        if path is not None:
            paths.append(path)
    if tool_name == "Glob":
        pattern = tool_input.get("pattern")
        if isinstance(pattern, str) and pattern:
            candidate = pattern if not _has_wildcard(pattern) else _glob_prefix(pattern)
            path = _resolve_tool_path(candidate, root)
            if path is not None:
                paths.append(path)
    return paths


def _glob_prefix(pattern: str) -> str:
    """取 glob 匹配式里第一个通配符之前的目录前缀。

    ``gathers/UPS/**/*.py`` → ``gathers/UPS``；``*/*.py`` → ``""``（无前缀）。
    前缀为空说明匹配式本身没有限定范围（如全局 ``**/*.py``），此时返回空串，
    由上层按"无路径"处理——这种宽匹配本来就不该放行。
    """
    head = pattern
    for index, char in enumerate(pattern):
        if char in "*?[":
            head = pattern[:index]
            break
    head = head.rstrip("/")
    # 去掉可能残留的文件名段：只保留目录层级。
    if head and "." in head.rsplit("/", 1)[-1]:
        head = head.rsplit("/", 1)[0] if "/" in head else ""
    return head


def _has_wildcard(value: str) -> bool:
    """判断值是否含 glob 通配符。含通配符的值不能当路径解析。"""
    return any(char in value for char in "*?[")


def resolve_tools(writable: bool, tools: Sequence[str] | None = None) -> list[str]:
    """解析 agent 可用工具集。

    未显式配置时按读写权限给出默认集合；只读 agent 永远拿不到写工具。
    """
    if tools:
        resolved = [str(item) for item in tools]
    else:
        resolved = ["Read", "Glob", "Grep"]
        if writable:
            resolved.extend(["Write", "Edit"])
    if not writable:
        resolved = [name for name in resolved if name not in _WRITE_TOOLS]
    allowed = [name for name in resolved if name in _ALL_TOOLS]
    return list(dict.fromkeys(allowed))


# 拒绝原因要结构化：模型会逐字读回这段文案。写成"禁止修改"会诱导它直接宣布
# "我无法修改"并放弃；明确指出"可以写进 change_plan 转分析报告"才是可用信息。
_SCOPE_DENIED_HINT = (
    "请核对 allowed_read_roots 中的真实路径。只读定位仅记录 source_evidence、"
    "evidence_gaps 和 recommended_scope，不要增加输出协议之外的字段。共享层禁止写入。"
)


def _mapped_tool_input(tool_name: str, values: dict[str, Any], root: Path, read_roots: Sequence[Path]) -> dict[str, Any]:
    """将逻辑相对路径映射到唯一获准的真实只读文件，SDK 和 hook 使用同一入参。"""
    if tool_name not in {"Read", "Glob", "Grep"}:
        return values
    mapped = dict(values)
    for key in ("file_path", "path"):
        raw = mapped.get(key)
        if not isinstance(raw, str) or not raw or ".." in Path(raw).parts:
            continue
        if Path(raw).is_absolute():
            requested = Path(raw).resolve()
            if any(_path_is_under(base, requested) for base in read_roots):
                continue
            # carrier 在任务副本中；把误拼到共享项目根的路径映射到唯一授权副本。
            for base in read_roots:
                if _path_is_under(root, base):
                    continue
                for ancestor in list(base.parents)[:3]:
                    if not _path_is_under(ancestor, requested):
                        continue
                    relative = requested.relative_to(ancestor)
                    if not relative.parts or relative.parts[0] not in {"gathers", "spiders"}:
                        continue
                    candidate = (root / relative).resolve()
                    if candidate.exists() and any(_path_is_under(allowed, candidate) for allowed in read_roots):
                        mapped[key] = str(candidate)
            continue
        primary = (root / raw).resolve()
        if primary.exists() and any(_path_is_under(base, primary) for base in read_roots):
            continue
        candidates: set[Path] = set()
        for base in read_roots:
            for ancestor in (base, *list(base.parents)[:3]):
                candidate = (ancestor / raw).resolve()
                if candidate.exists() and _path_is_under(base, candidate):
                    candidates.add(candidate)
        if len(candidates) == 1:
            mapped[key] = str(next(iter(candidates)))
    if tool_name == "Glob" and isinstance(mapped.get("pattern"), str):
        prefix = _glob_prefix(mapped["pattern"])
        if prefix:
            candidate = (root / prefix).resolve()
            search = _resolve_tool_path(mapped.get("path"), root)
            if search == root and candidate.exists() and any(_path_is_under(base, candidate) for base in read_roots):
                mapped["path"] = str(candidate)
                mapped["pattern"] = mapped["pattern"][len(prefix):].lstrip("/")
    return mapped


def _check_tool_access(
    tool_name: str,
    tool_input: dict[str, Any],
    root: Path,
    write_root: Path | Sequence[Path],
    allowed_tools: set[str],
    read_roots: Sequence[Path] | None = None,
) -> tuple[str, str]:
    """返回 PreToolUse 所需的允许/拒绝决定和原因。

    读和写用两份白名单：``read_roots`` 可以略宽（例如放进官方编码规范
    ``docs/rules.md``，提示词要求模型先读它），``write_root`` 始终只有命中的
    carrier 目录。写路径**不**享受读白名单的放宽。
    """
    if tool_name not in allowed_tools:
        return "deny", f"tool_not_allowed: 禁止使用工具 {tool_name}"
    paths = _tool_paths(tool_name, tool_input, root)
    if tool_name in {"Read", "Write", "Edit"} and not paths:
        return "deny", "invalid_input: 文件工具缺少路径"
    if tool_name in {"Glob", "Grep"} and not paths:
        return "deny", "invalid_input: 搜索必须限定在目标运输商目录"
    roots = [write_root] if isinstance(write_root, Path) else list(write_root)
    readable = list(read_roots) if read_roots else roots
    if tool_name in {"Glob", "Grep", "Read"} and any(
        not any(_path_is_under(base, path) for base in readable) for path in paths
    ):
        return "deny", f"scope_denied: 路径不在获准只读范围内。{_SCOPE_DENIED_HINT}"
    if tool_name in {"Write", "Edit"} and any(
        not any(_path_is_under(base, path) for base in roots) for path in paths
    ):
        return "deny", f"scope_denied: 目标 carrier 外文件不允许直接写入。{_SCOPE_DENIED_HINT}"
    return "allow", "允许访问工作区"


def _tool_brief(tool_name: Any, tool_input: Any) -> str:
    """把工具调用参数压成一行摘要。

    只写 ``tool=Grep`` 是没法排查的：得知道它到底在搜什么、读哪个文件，才能判断
    模型是在正常定位还是在原地打转（回合被耗光的典型原因）。
    """
    if not isinstance(tool_input, dict):
        return ""
    # 终端摘要只展示定位信息；命令正文和编辑前后文不进入日志。 
    keys = ("file_path", "path", "pattern", "glob", "description", "query")
    parts: list[str] = []
    for key in keys:
        value = tool_input.get(key)
        if value in (None, "", [], {}):
            continue
        text = " ".join(str(value).split())
        if len(text) > 60:
            text = f"{text[:60]}…"
        parts.append(f"{key}={text}")
        if len(parts) >= 3:
            break
    return " ".join(parts) or f"tool={tool_name}"


def _message_brief(message: Any) -> str:
    """把 SDK 消息压成一行可读摘要。

    只打 ``message_type=AssistantMessage`` 等于什么都没说：排查时要看的是模型
    到底讲了什么、工具返回了什么。这里按消息类型取正文——助手取文本块、用户
    取工具结果、结果消息取最终输出，并截断避免刷屏。
    """
    name = type(message).__name__
    text = ""
    if name == "AssistantMessage":
        chunks: list[str] = []
        for block in getattr(message, "content", None) or []:
            value = getattr(block, "text", None)
            if value:
                chunks.append(str(value))
        text = " ".join(chunks)
    elif name == "UserMessage":
        chunks = []
        for block in getattr(message, "content", None) or []:
            body = getattr(block, "content", None)
            if body:
                chunks.append(str(body))
        text = " ".join(chunks)
    elif name == "ResultMessage":
        text = str(getattr(message, "result", "") or "")
    else:
        subtype = getattr(message, "subtype", "")
        data = getattr(message, "data", None)
        if subtype == "thinking_tokens" and isinstance(data, dict):
            tokens = data.get("estimated_tokens")
            text = f"subtype=thinking_tokens tokens={tokens}" if tokens is not None else "subtype=thinking_tokens"
        else:
            text = f"subtype={subtype}" if subtype else ""
    text = " ".join(str(text).split())
    if not text:
        return ""
    limit = 400 if name == "AssistantMessage" else 200
    return text if len(text) <= limit else f"{text[:limit]}…"


def _flag(name: str, default: bool) -> bool:
    """环境变量可覆盖 settings；未设置时用项目默认值。

    解析口径与 ``config.settings.parse_bool`` 一致：``1/true/yes/on`` 都算真。
    此前这里只认字面量 ``true``，写成 ``1`` 会被静默当成关闭。
    """
    return parse_bool(os.getenv(name), default)


_ARCHIVE_PATH: Path | None = None


def _agent_archive_path() -> Path:
    """事件归档路径：显式配置优先，否则落在运行目录下。

    settings 里的默认值固定在仓库根目录；容器里 AI_REPAIR_RUNTIME_DIR 指向
    挂载卷时，事件归档会写到未挂载的位置，容器重启即丢。路径在进程内不变，
    只解析一次。
    """
    global _ARCHIVE_PATH
    if _ARCHIVE_PATH is not None:
        return _ARCHIVE_PATH
    from config.nacos import nacos_get
    configured = nacos_get("AI_REPAIR_DEBUG_HOOK_LOG", "").strip()
    if configured:
        _ARCHIVE_PATH = Path(configured).resolve()
        return _ARCHIVE_PATH
    try:
        from infrastructure.llm.config import load_config

        _ARCHIVE_PATH = Path(load_config().runtime_dir) / "agent-hooks.jsonl"
    except Exception:
        _ARCHIVE_PATH = ROOT / "runtime" / "agent-hooks.jsonl"
    return _ARCHIVE_PATH


_THINKING_OPEN = False


def _set_thinking(value: bool) -> None:
    """记录上一条进度是否还停在思考心跳行。"""
    global _THINKING_OPEN
    _THINKING_OPEN = value


def _debug_hook_event(event: dict[str, Any]) -> None:
    """按 settings 输出并落盘 Agent 对话流；环境变量可显式关闭。

    ``thinking_tokens`` 是思考阶段的心跳，不逐条刷屏，只在同一行刷新进度。
    """
    from config.settings import settings

    if _flag("AI_REPAIR_PROGRESS", True):
        event_type = event.get("event", "unknown")
        if event_type == "PreToolUse":
            detail = (
                f"tool={event.get('tool_name')} decision={event.get('decision')} "
                f"{_tool_brief(event.get('tool_name'), event.get('tool_input'))}"
            )
        elif event_type == "Message":
            message = event.get("message")
            message_type = str(event.get("message_type") or "")
            detail = f"message_type={message_type}"
            if message_type == "ResultMessage":
                brief = _message_brief(message)
                if brief:
                    detail = f"{detail} | {_clip(brief, 160)}"
            elif message_type == "AssistantMessage":
                # 不把模型的中间分析逐行打印到终端；只展示事件类别。
                content = getattr(message, "content", None)
                detail = f"{detail} | blocks={len(content) if isinstance(content, list) else 0}"
            elif message_type == "UserMessage":
                detail = f"{detail} | tool_result"
        else:
            detail = ""
        line = f"[agent] {time.strftime('%H:%M:%S')} {event_type} {detail}"
        # 终端仅输出事件摘要，不打印模型中间分析文本。
        print(line, file=sys.stderr, flush=True)
        _set_thinking(False)
    from infrastructure.storage.policy import debug_artifacts
    if not debug_artifacts() or not _flag("AI_REPAIR_DEBUG_HOOKS", True):
        return
    path = _agent_archive_path()
    # 调试文件只保留过滤后的事件摘要，不序列化完整 SDK 消息、Prompt 或工具正文。
    safe_event: dict[str, Any] = {"event": str(event.get("event") or "unknown")}
    if event.get("event") == "PreToolUse":
        safe_event.update({
            "tool_name": str(event.get("tool_name") or "")[:80],
            "decision": str(event.get("decision") or "")[:20],
            "detail": _tool_brief(event.get("tool_name"), event.get("tool_input"))[:240],
            "reason": _clip(event.get("reason") or "", 240),
        })
    elif event.get("event") == "Message":
        safe_event["message_type"] = str(event.get("message_type") or "")[:80]
        # 公开正文由按任务拆分的 agent_events.jsonl 负责；调试文件不重复存全文。
        safe_event["message_summary"] = (
            _message_brief(event.get("message"))[:160]
            if type(event.get("message")).__name__ == "ResultMessage" else ""
        )
    encoded = json.dumps(safe_event, ensure_ascii=False, separators=(",", ":")) + chr(10)
    max_bytes = 5 * 1024 * 1024
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size + len(encoded.encode("utf-8")) > max_bytes:
            rotated = path.with_suffix(path.suffix + ".1")
            if rotated.exists():
                rotated.unlink()
            path.replace(rotated)
        with path.open("a", encoding="utf-8") as file_obj:
            file_obj.write(encoded)
    except OSError:
        # 调试日志不能阻断 Agent 调用；任务级审计的持久化失败会单独记录。
        import logging
        logging.getLogger(__name__).exception("Agent 调试事件写入失败 path=%s", path)


def _sdk() -> tuple[Any, Any, Any]:
    """延迟加载 SDK，避免测试和服务启动时强制依赖 Claude 环境。"""
    try:
        from claude_agent_sdk import (
            ClaudeAgentOptions,
            ResultMessage,
            query,
        )
    except ImportError as error:
        raise ClaudeAgentUnavailable(
            "claude-agent-sdk 未安装，无法执行 Claude 智能体"
        ) from error
    return ClaudeAgentOptions, ResultMessage, query


async def _run(
    prompt: str,
    workspace: str | Path,
    env: dict[str, str] | None = None,
    writable: bool = False,
    tools: Sequence[str] | None = None,
    max_turns: int = 30,
    max_tool_calls: int = 32,
    allowed_roots: Sequence[str | Path] | None = None,
    allowed_write_roots: Sequence[str | Path] | None = None,
    output_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """在限定工具和工作区范围内运行一次 Claude 查询。"""
    ClaudeAgentOptions, ResultMessage, query = _sdk()
    from claude_agent_sdk import HookCallback, HookMatcher

    root = Path(workspace).resolve()
    raw_write_root = (env or {}).get("AI_REPAIR_WRITE_ROOT")
    write_root = Path(raw_write_root).resolve() if raw_write_root else root
    # 读与写两份白名单：allowed_roots 是读范围（可含共享只读源码树和官方规范文件），
    # write_root 是唯一可写目录。写范围必须落在任务工作区内；读范围由
    # PreToolUse 的白名单逐路径校验，可以指向任务工作区之外的共享只读树。
    read_roots = [Path(item).resolve() for item in (allowed_roots or [write_root])]
    try:
        write_root.relative_to(root)
        for allowed in allowed_write_roots or [write_root]:
            Path(allowed).resolve().relative_to(root)
    except ValueError as error:
        raise ValueError("AI 写入范围不在工作区内") from error
    from infrastructure.llm.config import load_config

    sdk_env = {
        **load_config().claude.as_env(),
        **{key: value for key, value in (env or {}).items() if key != "AI_REPAIR_WRITE_ROOT"},
    }
    # 仅内网模型网关证书过期时的临时开关；生产必须使用有效证书。
    if parse_bool(os.getenv("CLAUDE_AGENT_INSECURE_TLS"), default=False):
        sdk_env["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"
    file_tools = resolve_tools(writable, tools)

    budget = ToolBudget(max_tool_calls, writable=writable)

    async def pre_tool_use(hook_input: Any, tool_use_id: str | None, _context: Any) -> dict[str, Any]:
        name = hook_input.get("tool_name") if isinstance(hook_input, dict) else ""
        values = hook_input.get("tool_input") if isinstance(hook_input, dict) else {}
        if not isinstance(name, str) or not isinstance(values, dict):
            name, values = str(name or ""), {}
            decision, reason = "deny", "工具输入不是有效对象"
        else:
            values = _mapped_tool_input(name, values, root, read_roots)
            decision, reason = _check_tool_access(name, values, root,
                [Path(item).resolve() for item in (allowed_write_roots or [write_root])], set(file_tools), read_roots)
        decision, reason = budget.decide(name, values, decision, reason)
        events.append(_decision_event(name, values, tool_use_id, decision, reason, started_at))
        _debug_hook_event({"event": "PreToolUse", "tool_name": name, "tool_input": values,
                           "decision": decision, "reason": reason})
        return budget.hook_output(name, values, decision, reason)

    options = ClaudeAgentOptions(
        **({"output_format": {"type": "json_schema", "schema": output_schema}} if output_schema else {}),
        cwd=str(Path(workspace).resolve()),
        tools=file_tools,
        allowed_tools=file_tools,
        disallowed_tools=[name for name in _ALL_TOOLS if name not in file_tools],
        permission_mode="dontAsk",
        max_turns=max_turns + 3,  # SDK 统一计轮，另留 3 轮完成修改与结构化交接。
        setting_sources=[],
        hooks={
            "PreToolUse": [
                HookMatcher(
                    matcher="|".join(file_tools),
                    hooks=[cast(HookCallback, pre_tool_use)],
                )
            ]
        },
        env=sdk_env,
    )

    async def prompt_stream() -> Any:
        """以 SDK 要求的 streaming 输入模式提交带权限回调的提示词。"""
        yield {
            "type": "user",
            "message": {"role": "user", "content": prompt},
            "parent_tool_use_id": None,
            "session_id": "",
        }

    result: Any = None
    events: list[dict[str, Any]] = []
    started_at = time.perf_counter()
    try:
        async for message in query(prompt=prompt_stream(), options=options):
            # SDK max_turns counts model/tool rounds; streamed message fragments must not consume another budget.
            _collect_events(events, message, started_at)
            _debug_hook_event(
                {
                    "event": "Message",
                    "message_type": type(message).__name__,
                    "message": message,
                }
            )
            if isinstance(message, ResultMessage):
                result = message
    except Exception as error:
        # SDK 在流结束前异常时也把已经收到的有效事件交给调用审计层落盘。
        try:
            setattr(error, "agent_events", events)
            setattr(error, "agent_usage", {**_tokens(getattr(result, "usage", None)), **budget.usage(events)})
            setattr(error, "sdk_receipt", _sdk_receipt(result))
        except Exception:
            pass
        raise
    if result is None:
        error = RuntimeError("Claude 智能体未返回结果")
        setattr(error, "agent_events", events)
        setattr(error, "agent_usage", {**_tokens(None), **budget.usage(events)})
        raise error
    if result.is_error:
        hint = ""
        if result.subtype == "error_max_turns":
            hint = f"（SDK 轮次上限 {max_turns + 3} 已耗尽，包含 3 轮收尾余量）"
        # result.result 是真正的错误消息，subtype 只是类型标签（可能是 "success"）
        error_msg = result.result or f"SDK 标记调用失败（is_error=true, subtype={result.subtype or 'unknown'}），但未提供错误正文"
        error = RuntimeError(f"Claude 智能体执行失败: {error_msg}{hint}")
        setattr(error, "agent_events", events)
        setattr(error, "agent_usage", {**_tokens(getattr(result, "usage", None)), **budget.usage(events)})
        setattr(error, "sdk_receipt", _sdk_receipt(result))
        raise error
    structured = getattr(result, "structured_output", None)
    text = json.dumps(structured, ensure_ascii=False) if isinstance(structured, dict) else result.result or ""
    usage = getattr(result, "usage", None)
    tokens = _tokens(usage)
    return {
        "passed": True,
        "result": text,
        "messages": events,
        "sdk_receipt": _sdk_receipt(result),
        "usage": {
            **budget.usage(events),
            "prompt_chars": len(prompt),
            "result_chars": len(text),
            **tokens,
        },
    }


def run_agent_once(
    prompt: str,
    workspace: str | Path,
    env: dict[str, str] | None = None,
    writable: bool = False,
    tools: Sequence[str] | None = None,
    max_turns: int = 30,
    max_tool_calls: int = 32,
    timeout_seconds: int = 900,
    allowed_roots: Sequence[str | Path] | None = None,
    allowed_write_roots: Sequence[str | Path] | None = None,
    output_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """以一次性查询方式运行智能体，按配置限定工具和读写权限。"""
    return asyncio.run(
        asyncio.wait_for(_run(
            prompt,
            workspace,
            env,
            writable=writable,
            tools=tools,
            max_turns=max_turns,
            max_tool_calls=max_tool_calls,
            allowed_roots=allowed_roots,
            allowed_write_roots=allowed_write_roots,
            output_schema=output_schema,
        ), timeout=max(1, int(timeout_seconds)))
    )


class ClaudeSession:
    """执行级常驻 Claude 会话，按队列顺序处理多个 carrier 任务。

    工具集、读写权限和最大轮次由 agent 配置注入，会话本身不假设角色。
    """

    def __init__(
        self,
        workspace: str | Path,
        env: dict[str, str],
        init_prompt: str,
        *,
        tools: Sequence[str] | None = None,
        writable: bool = True,
        max_turns: int = 30,
        max_tool_calls: int = 32,
        timeout_seconds: int = 900,
        **kwargs
    ) -> None:
        """启动后台 SDK 生命周期并加载一次初始化 Prompt。"""
        self._workspace = Path(workspace).resolve()
        self._env = dict(env)
        self._init_prompt = init_prompt
        self._output_schema = kwargs.get("output_schema")
        self._cumulative_budget = bool(kwargs.get("cumulative_budget", False))
        self._deadline = time.monotonic() + timeout_seconds if self._cumulative_budget else None
        self._tools = resolve_tools(writable, tools)
        self._max_turns = max(1, int(max_turns))
        self._max_tool_calls = max(1, int(max_tool_calls))
        self._timeout_seconds = max(1, int(timeout_seconds))
        self._ready = threading.Event()
        self._startup_error: str = ""
        self._requests: queue.Queue[
            tuple[str, Sequence[Path], Path, str | None, queue.Queue[dict[str, Any]] | None]
        ] = queue.Queue()
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()

    @property
    def tools(self) -> list[str]:
        """返回本会话实际允许的工具集。"""
        return list(self._tools)

    def invoke(
        self,
        prompt: str,
        write_root: str | Path,
        context_prompt: str | None = None,
        allowed_roots: Sequence[str | Path] | None = None,
        allowed_write_roots: Sequence[str | Path] | None = None,
    ) -> dict[str, Any]:
        """提交一个任务，复用同一 SDK 会话并等待结果。"""
        result: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        read_roots = [Path(item).resolve() for item in (allowed_roots or [write_root])]
        write_roots = [Path(item).resolve() for item in (allowed_write_roots or [write_root])]
        if any(not path.is_relative_to(self._workspace) for path in write_roots):
            raise ValueError("写入范围越出任务工作区")
        self._requests.put((prompt, read_roots, write_roots, context_prompt, result))
        deadline = time.monotonic() + self._timeout_seconds + 30
        while time.monotonic() < deadline:
            if self._startup_error:
                return {"passed": False, "reason": "AGENT_STARTUP_FAILED",
                        "failure_kind": "agent_startup_failed", "failure_detail": self._startup_error}
            try:
                return result.get(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                if self._startup_error:
                    continue
                if not self._thread.is_alive():
                    return {"passed": False, "reason": "AGENT_SESSION_CLOSED",
                            "failure_kind": "agent_session_closed", "failure_detail": "模型会话线程已退出，未返回结论"}
        self.close()
        return {"passed": False, "reason": "AGENT_TIMEOUT", "failure_kind": "agent_timeout"}

    def close(self) -> None:
        """关闭后台会话。"""
        self._requests.put(("", [self._workspace], self._workspace, None, None))
        self._thread.join(timeout=30)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._serve())
        except Exception as error:
            from infrastructure.storage.redaction import redact_for_delivery
            self._startup_error = f"模型会话启动或连接失败：{type(error).__name__}: {redact_for_delivery(str(error))[:1500]}"

    def _sdk_env(self) -> dict[str, str]:
        """常驻会话也必须拿到模型网关凭证与 TLS 开关。"""
        from infrastructure.llm.config import load_config

        env = {**load_config().claude.as_env(), **self._env}
        env.pop("AI_REPAIR_WRITE_ROOT", None)
        if parse_bool(os.getenv("CLAUDE_AGENT_INSECURE_TLS"), default=False):
            env["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"
        return env

    async def _serve(self) -> None:
        validate_process_runtime()
        from claude_agent_sdk import (
            ClaudeAgentOptions,
            ClaudeSDKClient,
            HookCallback,
            HookMatcher,
            ResultMessage,
        )

        current_read_roots: Sequence[Path] = [self._workspace]
        current_write_root: Path | Sequence[Path] = self._workspace
        tools = list(self._tools)
        budget = ToolBudget(self._max_tool_calls, writable="Edit" in self._tools or "Write" in self._tools)
        events: list[dict[str, Any]] = []
        started_at = time.perf_counter()

        async def pre_tool_use(hook_input: Any, tool_use_id: str | None, _context: Any) -> dict[str, Any]:
            name = hook_input.get("tool_name") if isinstance(hook_input, dict) else ""
            values = hook_input.get("tool_input") if isinstance(hook_input, dict) else {}
            if not isinstance(name, str) or not isinstance(values, dict):
                name, values = str(name or ""), {}
                decision, reason = "deny", "工具输入不是有效对象"
            else:
                values = _mapped_tool_input(name, values, self._workspace, current_read_roots)
                decision, reason = _check_tool_access(name, values, self._workspace, current_write_root,
                                                      set(tools), current_read_roots)
            decision, reason = budget.decide(name, values, decision, reason)
            events.append(_decision_event(name, values, tool_use_id, decision, reason, started_at))
            _debug_hook_event({"event": "PreToolUse", "tool_name": name, "tool_input": values,
                               "decision": decision, "reason": reason})
            return budget.hook_output(name, values, decision, reason)

        options = ClaudeAgentOptions(
            **({"output_format": {"type": "json_schema", "schema": self._output_schema}} if getattr(self, "_output_schema", None) else {}),
            cwd=str(self._workspace),
            tools=tools,
            allowed_tools=tools,
            disallowed_tools=[name for name in _ALL_TOOLS if name not in tools],
            permission_mode="dontAsk",
            max_turns=self._max_turns + 3,
            setting_sources=[],
            env=self._sdk_env(),
            hooks={
                "PreToolUse": [
                    HookMatcher(
                        matcher="|".join(tools),
                        hooks=[cast(HookCallback, pre_tool_use)],
                    )
                ]
            },
        )
        client = ClaudeSDKClient(options=options)
        await client.connect()
        # 连接完成即可接任务。初始化规则随首个任务发送，不再空转一轮。
        self._ready.set()
        while True:
            prompt, read_roots, write_root, context_prompt, result_queue = await asyncio.to_thread(
                self._requests.get
            )
            if result_queue is None:
                break
            current_read_roots = read_roots
            current_write_root = write_root
            # 即使 SDK 在首条消息前异常，失败响应也要有一致的 events 字段。
            # invoke_with_audit 会将已收集事件持久化到当前 task，而不是只保存失败元数据。
            events: list[dict[str, Any]] = []
            try:
                if not self._cumulative_budget:
                    budget = ToolBudget(self._max_tool_calls, writable="Edit" in self._tools or "Write" in self._tools)
                usage_start = budget.usage([])
                def call_usage() -> dict[str, Any]:
                    usage = budget.usage(events)
                    for key in ("tool_calls", "tool_attempts", "tool_allowed", "tool_denied", "tool_blocked_writes"):
                        usage[key] -= usage_start[key]
                    return usage
                result = None
                if context_prompt:
                    prompt = f"{context_prompt}\n\n{prompt}"
                started_at = time.perf_counter()
                result: Any = None
                remaining = max(0.001, self._deadline - time.monotonic()) if self._deadline is not None else self._timeout_seconds
                async with asyncio.timeout(remaining):
                    await client.query(self._with_rules(prompt))
                    async for message in client.receive_response():
                        # Round limits belong to the SDK, not streamed text/thinking fragments.
                        _collect_events(events, message, started_at)
                        _debug_hook_event(
                            {
                                "event": "Message",
                                "message_type": type(message).__name__,
                                "message": message,
                            }
                        )
                        if isinstance(message, ResultMessage):
                            result = message
                if result is None or result.is_error:
                    raise RuntimeError(f"Agent 返回错误: {getattr(result, 'subtype', 'empty')}：{getattr(result, 'result', '') or ''}")
                structured = getattr(result, "structured_output", None)
                text = json.dumps(structured, ensure_ascii=False) if isinstance(structured, dict) else result.result or ""
                result_queue.put({
                    "passed": True,
                    "result": text,
                    "messages": events,
                    "sdk_receipt": _sdk_receipt(result),
                    "usage": {
                        **call_usage(),
                        "prompt_chars": len(prompt),
                        "result_chars": len(text),
                        **_tokens(getattr(result, "usage", None)),
                    },
                })
            except Exception as error:
                reason = (
                    "AGENT_TIMEOUT"
                    if isinstance(error, TimeoutError)
                    else f"常驻 Agent 调用失败（错误类型：{type(error).__name__}）"
                )
                # 不能把已收到的工具事件随异常一起丢弃；审计层会过滤并按 task 落盘。
                result_queue.put({
                    "passed": False,
                    "reason": reason,
                    "failure_detail": str(error),
                    "failure_kind": budget.stop_kind or ("agent_timeout" if isinstance(error, TimeoutError) else "provider_error"),
                    "messages": events,
                    "sdk_receipt": _sdk_receipt(result),
                    "usage": {
                        **call_usage(),
                        "prompt_chars": len(prompt),
                        "result_chars": 0,
                        **_tokens(getattr(result, "usage", None)),
                    },
                })
                if isinstance(error, TimeoutError):
                    break
        await client.disconnect()

    def _with_rules(self, prompt: str) -> str:
        """首个任务带上初始化规则，之后只发任务。"""
        if not self._init_prompt:
            return prompt
        rules = self._init_prompt
        self._init_prompt = ""
        return f"{rules}\n\n## 当前任务\n{prompt}"
