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

_ALL_TOOLS = (
    "Read",
    "Glob",
    "Grep",
    "Write",
    "Edit",
    "Bash",
    "Task",
    "WebFetch",
    "WebSearch",
    "Skill",
    "NotebookEdit",
)
_WRITE_TOOLS = frozenset({"Write", "Edit", "NotebookEdit"})


class ClaudeAgentUnavailable(RuntimeError):
    """Claude Agent SDK 未安装或不可用。"""

    pass


_MAX_EVENT_TEXT = 2000
_MAX_EVENT_VALUE = 200
_MAX_EVENTS = 200
_TOOL_INPUT_KEYS = (
    "file_path",
    "notebook_path",
    "pattern",
    "path",
    "command",
    "description",
    "url",
    "query",
    "prompt",
    "skill",
    "glob",
    "old_string",
    "new_string",
    "content",
)


def _tokens(usage: Any) -> dict[str, int]:
    """从 SDK 结果里取 token。没有用量时记 0，不把调用统计冒充 token。"""
    if not isinstance(usage, dict):
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    total = usage.get("total_tokens")
    if not isinstance(total, int):
        total = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": int(total),
    }


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
    """工具入参只保留定位信息与长度。

    整段代码或正文不重复进事件记录：既没有排查价值，又会让记录体积翻倍。
    """
    if not isinstance(tool_input, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in _TOOL_INPUT_KEYS:
        if key not in tool_input:
            continue
        item = tool_input[key]
        summary[key] = _clip(item, _MAX_EVENT_VALUE) if isinstance(item, str) else item
    return summary


def _public_events(message: Any) -> list[dict[str, Any]]:
    """把 SDK 消息压成"有内容的事件"，空消息直接丢弃。

    旧实现只取 text block：工具调用、系统消息、思考块全部变成
    ``{"type": ..., "text": ""}``，事件记录里一半是空条目。现在按内容拆分：
    助手文本、工具调用（工具名 + 定位参数）、工具结果（含错误标记）；
    隐藏思考与凭据仍然不进记录。
    """
    events: list[dict[str, Any]] = []
    content = getattr(message, "content", None)
    if isinstance(content, str):
        if content.strip():
            events.append({"type": "assistant", "text": _clip(content, _MAX_EVENT_TEXT)})
        return events
    if not isinstance(content, list):
        return events
    for block in content:
        tool_name = getattr(block, "name", None)
        tool_input = getattr(block, "input", None)
        if isinstance(tool_name, str) and tool_input is not None:
            events.append(
                {"type": "tool_use", "tool": tool_name, "input": _tool_input_summary(tool_input)}
            )
            continue
        if hasattr(block, "tool_use_id"):
            events.append(
                {
                    "type": "tool_result",
                    "is_error": bool(getattr(block, "is_error", False)),
                    "text": _clip(_text_of(getattr(block, "content", "")), _MAX_EVENT_VALUE * 4),
                }
            )
            continue
        text = getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            events.append({"type": "assistant", "text": _clip(text, _MAX_EVENT_TEXT)})
    return events


def _collect_events(events: list[dict[str, Any]], message: Any) -> None:
    """把一条 SDK 消息展开进事件列表，并按上限截断。"""
    for event in _public_events(message):
        if len(events) >= _MAX_EVENTS:
            return
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
# "我无法修改"并放弃；明确指出"可以写进 change_plan 走人工授权"才是可用信息。
_SCOPE_DENIED_HINT = (
    "该路径在目标 carrier 之外。若根因确实在此，请不要放弃：把修复方案写进输出 JSON 的 "
    "change_plan（scope=project、requires_human_authorization=true、edits[].content 给完整文件内容），"
    "系统会转人工审批并在批准后落盘。"
)


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
        return "deny", f"scope_denied: 只允许读取目标运输商目录。{_SCOPE_DENIED_HINT}"
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
    keys = ("file_path", "path", "pattern", "glob", "command", "old_string", "new_string")
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
    """环境变量可覆盖 settings；未设置时用项目默认值。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() == "true"


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

    if _flag("AI_REPAIR_PROGRESS", settings.agent_progress):
        event_type = event.get("event", "unknown")
        thinking = False
        if event_type == "PreToolUse":
            detail = (
                f"tool={event.get('tool_name')} decision={event.get('decision')} "
                f"{_tool_brief(event.get('tool_name'), event.get('tool_input'))}"
            )
        elif event_type == "Message":
            detail = f"message_type={event.get('message_type')}"
            brief = _message_brief(event.get("message")) if event.get("message") is not None else ""
            thinking = "subtype=thinking_tokens" in brief
            if brief:
                detail = f"{detail} | {brief}"
        else:
            detail = ""
        line = f"[agent] {time.strftime('%H:%M:%S')} {event_type} {detail}"
        if thinking:
            print(f"\r{line}", end="", file=sys.stderr, flush=True)
        else:
            print(f"\n{line}" if _THINKING_OPEN else line, file=sys.stderr, flush=True)
        _set_thinking(thinking)
    if not _flag("AI_REPAIR_DEBUG_HOOKS", settings.agent_archive):
        return
    path_value = os.getenv("AI_REPAIR_DEBUG_HOOK_LOG", "").strip()
    path = Path(path_value).resolve() if path_value else settings.agent_archive_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file_obj:
        file_obj.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


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
) -> dict[str, Any]:
    """在限定工具和工作区范围内运行一次 Claude 查询。"""
    ClaudeAgentOptions, ResultMessage, query = _sdk()
    from claude_agent_sdk import HookCallback, HookMatcher

    root = Path(workspace).resolve()
    raw_write_root = (env or {}).get("AI_REPAIR_WRITE_ROOT")
    write_root = Path(raw_write_root).resolve() if raw_write_root else root
    # 读与写两份白名单：allowed_roots 是读范围（可含官方规范文件），
    # write_root 是唯一可写目录。两者都必须落在工作区内。
    read_roots = [Path(item).resolve() for item in (allowed_roots or [write_root])]
    try:
        for allowed_root in read_roots:
            allowed_root.relative_to(root)
    except ValueError as error:
        raise ValueError("AI 访问范围不在工作区内") from error
    sdk_env = {key: value for key, value in (env or {}).items() if key != "AI_REPAIR_WRITE_ROOT"}
    file_tools = resolve_tools(writable, tools)

    tool_count = 0

    async def pre_tool_use(
        hook_input: Any,
        _tool_use_id: str | None,
        _context: Any,
    ) -> dict[str, Any]:
        """在每次工具调用前强制执行工具类型和路径边界。"""
        nonlocal tool_count
        tool_count += 1
        tool_name = hook_input.get("tool_name")
        tool_input = hook_input.get("tool_input")
        if tool_count > max_tool_calls:
            decision, reason = "deny", f"工具调用超过预算 {max_tool_calls}"
        elif not isinstance(tool_name, str):
            decision, reason = "deny", "工具名称无效"
        elif not isinstance(tool_input, dict):
            decision, reason = "deny", "工具输入不是对象"
        else:
            decision, reason = _check_tool_access(
                tool_name,
                tool_input,
                root,
                write_root,
                set(file_tools),
                read_roots,
            )
        _debug_hook_event(
            {
                "event": "PreToolUse",
                "tool_name": tool_name,
                "tool_input": tool_input,
                "decision": decision,
                "reason": reason,
            }
        )
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": reason,
            }
        }

    options = ClaudeAgentOptions(
        cwd=str(Path(workspace).resolve()),
        tools=file_tools,
        allowed_tools=file_tools,
        disallowed_tools=[name for name in _ALL_TOOLS if name not in file_tools],
        permission_mode="dontAsk",
        max_turns=max_turns,
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
    async for message in query(prompt=prompt_stream(), options=options):
        _collect_events(events, message)
        _debug_hook_event(
            {
                "event": "Message",
                "message_type": type(message).__name__,
                "message": message,
            }
        )
        if isinstance(message, ResultMessage):
            result = message
    if result is None:
        raise RuntimeError("Claude 智能体未返回结果")
    if result.is_error:
        hint = ""
        if result.subtype == "error_max_turns":
            hint = f"（回合预算 {max_turns} 已耗尽：模型把预算花在探索上，没有输出结论）"
        # result.result 是真正的错误消息，subtype 只是类型标签（可能是 "success"）
        error_msg = result.result or result.subtype or "未知错误"
        raise RuntimeError(f"Claude 智能体执行失败: {error_msg}{hint}")
    text = result.result or ""
    usage = getattr(result, "usage", None)
    tokens = _tokens(usage)
    return {
        "passed": True,
        "result": text,
        "messages": events,
        "usage": {
            "tool_calls": tool_count,
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
        ), timeout=max(1, int(timeout_seconds)))
    )


def run_repair_agent(
    prompt: str,
    workspace: str | Path,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """运行允许修改隔离工作区的 Claude 修复智能体。"""
    return asyncio.run(_run(prompt, workspace, env, writable=True))


def run_review_agent(
    prompt: str,
    workspace: str | Path,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """运行只读 Claude 代码审查智能体。"""
    return asyncio.run(_run(prompt, workspace, env, writable=False))


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
        self._tools = resolve_tools(writable, tools)
        self._max_turns = max(1, int(max_turns))
        self._max_tool_calls = max(1, int(max_tool_calls))
        self._timeout_seconds = max(1, int(timeout_seconds))
        self._ready = threading.Event()
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
    ) -> dict[str, Any]:
        """提交一个任务，复用同一 SDK 会话并等待结果。"""
        result: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        read_roots = [Path(item).resolve() for item in (allowed_roots or [write_root])]
        self._requests.put((prompt, read_roots, Path(write_root).resolve(), context_prompt, result))
        try:
            return result.get(timeout=self._timeout_seconds + 30)
        except queue.Empty:
            self.close()
            return {"passed": False, "reason": "AGENT_TIMEOUT"}

    def close(self) -> None:
        """关闭后台会话。"""
        self._requests.put(("", [self._workspace], self._workspace, None, None))
        self._thread.join(timeout=30)

    def _thread_main(self) -> None:
        asyncio.run(self._serve())

    async def _serve(self) -> None:
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
        tool_count = 0

        async def pre_tool_use(
            hook_input: Any, _tool_id: str | None, _context: Any
        ) -> dict[str, Any]:
            nonlocal tool_count
            tool_count += 1
            tool_name = hook_input.get("tool_name")
            tool_input = hook_input.get("tool_input")
            if tool_count > self._max_tool_calls:
                decision, reason = "deny", f"工具调用超过预算 {self._max_tool_calls}"
            elif not isinstance(tool_name, str) or not isinstance(tool_input, dict):
                decision, reason = "deny", "工具输入无效"
            else:
                decision, reason = _check_tool_access(
                    tool_name,
                    tool_input,
                    self._workspace,
                    current_write_root,
                    set(tools),
                    current_read_roots,
                )
            _debug_hook_event(
                {
                    "event": "PreToolUse",
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "decision": decision,
                    "reason": reason,
                }
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": reason,
                }
            }

        options = ClaudeAgentOptions(
            cwd=str(self._workspace),
            tools=tools,
            allowed_tools=tools,
            disallowed_tools=[name for name in _ALL_TOOLS if name not in tools],
            permission_mode="dontAsk",
            max_turns=self._max_turns,
            setting_sources=[],
            env={k: v for k, v in self._env.items() if k != "AI_REPAIR_WRITE_ROOT"},
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
            try:
                tool_count = 0
                if context_prompt:
                    await client.query(context_prompt)
                    async for _ in client.receive_response():
                        pass
                await client.query(self._with_rules(prompt))
                result: Any = None
                events: list[dict[str, Any]] = []
                async with asyncio.timeout(self._timeout_seconds):
                    async for message in client.receive_response():
                        _collect_events(events, message)
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
                    raise RuntimeError(f"Agent 返回错误: {getattr(result, 'subtype', 'empty')}")
                result_queue.put({
                    "passed": True,
                    "result": result.result or "",
                    "messages": events,
                    "usage": {
                        "tool_calls": tool_count,
                        "prompt_chars": len(prompt),
                        "result_chars": len(result.result or ""),
                    },
                })
            except Exception as error:
                reason = "AGENT_TIMEOUT" if isinstance(error, TimeoutError) else f"常驻 Agent 调用失败: {error}"
                result_queue.put({"passed": False, "reason": reason})
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
