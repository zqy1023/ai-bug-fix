"""修复 agent 的独立配置。

配置只属于修复 agent，其它 agent 不读取、不共享：

- 文件：``app/agents/repair/config.json``（可用 ``AI_REPAIR_AGENT_REPAIR_CONFIG`` 换路径）
- 环境变量覆盖：``AI_REPAIR_AGENT_REPAIR_<字段>``
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from config.settings import parse_bool

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PATH = Path(__file__).resolve().parent / "config.json"
ENV_PREFIX = "AI_REPAIR_AGENT_REPAIR_"
WRITE_TOOLS = ("Read", "Glob", "Grep", "Write", "Edit")

DEFAULTS: dict[str, Any] = {
    "description": "分析异常并执行最小修改",
    "enabled": True,
    "provider": "claude_session",
    "writable": True,
    "tools": list(WRITE_TOOLS),
    "max_turns": 16,
    "max_tool_calls": 20,
    "max_context_chars": 18000,
    "max_candidate_files": 6,
    "max_diff_chars": 12000,
    "timeout_seconds": 480,
    "options": {},
}

class RepairConfigError(ValueError):
    """修复 agent 配置非法。"""


@dataclass(frozen=True)
class RepairConfig:
    """修复 agent 的运行配置。"""

    # description：配置用途说明。
    description: str = ""
    # enabled：是否启用 Agent。
    enabled: bool = True
    # provider：Agent 模型调用适配器。
    provider: str = "claude_session"
    # writable：是否允许 Agent 写文件。
    writable: bool = True
    # tools：允许使用的工具白名单。
    tools: tuple[str, ...] = WRITE_TOOLS
    # max_turns：模型交互回合上限。
    max_turns: int = 16
    # max_tool_calls：工具调用总数上限。
    max_tool_calls: int = 20
    # max_context_chars：模型上下文字符上限。
    max_context_chars: int = 18000
    # max_candidate_files：候选文件数量上限。
    max_candidate_files: int = 6
    # max_diff_chars：差异证据字符上限。
    max_diff_chars: int = 12000
    # timeout_seconds：单次 Agent 超时秒数。
    timeout_seconds: int = 480
    # options：Provider 扩展参数。
    options: Mapping[str, Any] = field(default_factory=dict)
    # source：当前配置来源。
    source: str = "defaults"

    @classmethod
    def load(
        cls,
        path: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> RepairConfig:
        """加载配置：默认值 ← 配置文件 ← 环境变量。"""
        environment = dict(env if env is not None else os.environ)
        raw_path = str(path or environment.get(f"{ENV_PREFIX}CONFIG", "")).strip()
        resolved = Path(raw_path) if raw_path else DEFAULT_PATH
        payload = dict(DEFAULTS)
        source = "defaults"
        if resolved.is_file():
            loaded = json.loads(resolved.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise RepairConfigError(f"修复 agent 配置必须是对象: {resolved}")
            payload.update(loaded)
            source = str(resolved)
        force_writable = environment.get(f"{ENV_PREFIX}WRITABLE", "").strip()
        if force_writable:
            payload["writable"] = parse_bool(force_writable)
        enables = environment.get("AI_REPAIR_AGENT_REPAIR_ENABLED", "").strip()
        if not enables:
            enables = environment.get(f"{ENV_PREFIX}ENABLED", "").strip()
        if enables:
            payload["enabled"] = parse_bool(enables)
        provider = environment.get(f"{ENV_PREFIX}PROVIDER", "").strip()
        if provider:
            payload["provider"] = provider
        turns = environment.get(f"{ENV_PREFIX}MAX_TURNS", "").strip()
        if turns:
            payload["max_turns"] = int(turns)
        for field_name in (
            "MAX_TOOL_CALLS",
            "MAX_CONTEXT_CHARS",
            "MAX_CANDIDATE_FILES",
            "MAX_DIFF_CHARS",
            "TIMEOUT_SECONDS",
        ):
            value = environment.get(f"{ENV_PREFIX}{field_name}", "").strip()
            if value:
                payload[field_name.lower()] = int(value)
        tools = environment.get(f"{ENV_PREFIX}TOOLS", "").strip()
        if tools:
            payload["tools"] = [item.strip() for item in tools.split(",") if item.strip()]
        return cls.from_dict(payload, source=source)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], source: str = "inline") -> RepairConfig:
        """校验并构造，非法值立即报错而不是静默取默认值。"""
        enabled = payload.get("enabled", True)
        if not isinstance(enabled, bool):
            raise RepairConfigError("repair.enabled 必须是布尔值")
        writable = payload.get("writable", True)
        if not isinstance(writable, bool):
            raise RepairConfigError("repair.writable 必须是布尔值")
        tools = payload.get("tools")
        if not isinstance(tools, list) or not all(isinstance(item, str) for item in tools):
            raise RepairConfigError("repair.tools 必须是字符串数组")
        if tuple(tools) != WRITE_TOOLS:
            raise RepairConfigError("repair.tools 必须严格为 Read、Glob、Grep、Write、Edit")
        max_turns = payload.get("max_turns", 16)
        if not isinstance(max_turns, int) or max_turns < 1:
            raise RepairConfigError("repair.max_turns 必须是正整数")
        limits = {key: payload.get(key, default) for key, default in (("max_tool_calls", 20), ("max_context_chars", 18000), ("max_candidate_files", 6), ("max_diff_chars", 12000))}
        if any(not isinstance(value, int) or value < 1 for value in limits.values()):
            raise RepairConfigError("repair agent budget 必须是正整数")
        timeout = payload.get("timeout_seconds", 480)
        if not isinstance(timeout, int) or timeout < 1:
            raise RepairConfigError("repair.timeout_seconds 必须是正整数")
        provider = payload.get("provider", "claude_session")
        if not isinstance(provider, str) or not provider:
            raise RepairConfigError("repair.provider 必须是非空字符串")
        options = payload.get("options") or {}
        if not isinstance(options, dict):
            raise RepairConfigError("repair.options 必须是对象")
        return cls(
            description=str(payload.get("description", "")),
            enabled=enabled,
            provider=provider,
            writable=writable,
            tools=tuple(str(item) for item in tools),
            max_turns=int(max_turns),
            max_tool_calls=int(limits["max_tool_calls"]),
            max_context_chars=int(limits["max_context_chars"]),
            max_candidate_files=int(limits["max_candidate_files"]),
            max_diff_chars=int(limits["max_diff_chars"]),
            timeout_seconds=int(timeout),
            options=dict(options),
            source=source,
        )

    def with_overrides(self, **changes: Any) -> RepairConfig:
        """派生一份覆盖后的配置。"""
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        """序列化用于展示。"""
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "writable": self.writable,
            "tools": list(self.tools),
            "max_turns": self.max_turns,
            "max_tool_calls": self.max_tool_calls,
            "max_context_chars": self.max_context_chars,
            "max_candidate_files": self.max_candidate_files,
            "max_diff_chars": self.max_diff_chars,
            "timeout_seconds": self.timeout_seconds,
            "description": self.description,
            "options": dict(self.options),
            "source": self.source,
        }


__all__ = [
    "DEFAULTS",
    "DEFAULT_PATH",
    "ENV_PREFIX",
    "RepairConfig",
    "RepairConfigError",
    "WRITE_TOOLS",
]
