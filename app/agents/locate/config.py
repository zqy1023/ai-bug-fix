"""根因定位 agent 的独立、只读运行配置。"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PATH = Path(__file__).resolve().parent / "config.json"
ENV_PREFIX = "AI_REPAIR_AGENT_LOCATE_"
READ_TOOLS = ("Read", "Glob", "Grep")


class LocateConfigError(ValueError):
    """定位 agent 配置非法或试图放宽只读权限。"""


@dataclass(frozen=True)
class LocateConfig:
    """定位模型预算；工具与读写权限不可由配置放宽。"""

    provider: str
    max_turns: int
    max_tool_calls: int
    max_context_chars: int
    max_candidate_files: int
    timeout_seconds: int
    source: str

    @classmethod
    def load(
        cls,
        path: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> LocateConfig:
        environment = dict(env if env is not None else os.environ)
        raw_path = str(path or environment.get(f"{ENV_PREFIX}CONFIG", "")).strip()
        resolved = Path(raw_path) if raw_path else DEFAULT_PATH
        if not resolved.is_file():
            raise LocateConfigError(f"缺少定位 agent 配置文件: {resolved}")
        try:
            payload = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise LocateConfigError(f"无法读取定位 agent 配置: {resolved}") from error
        if not isinstance(payload, dict):
            raise LocateConfigError("locate 配置必须是 JSON 对象")
        merged = dict(payload)
        for key in ("provider", "max_turns", "max_tool_calls", "max_context_chars", "max_candidate_files", "timeout_seconds"):
            value = environment.get(f"{ENV_PREFIX}{key.upper()}", "").strip()
            if value:
                merged[key] = value if key == "provider" else int(value)
        return cls.from_dict(merged, source=str(resolved))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], source: str = "inline") -> LocateConfig:
        if payload.get("writable", False) is not False:
            raise LocateConfigError("locate.writable 必须为 false")
        tools = payload.get("tools", list(READ_TOOLS))
        if not isinstance(tools, list) or tuple(tools) != READ_TOOLS:
            raise LocateConfigError("locate.tools 必须严格为 Read、Glob、Grep")
        provider = payload.get("provider")
        if provider != "claude_query":
            raise LocateConfigError("locate.provider 当前只支持 claude_query")
        values: dict[str, int] = {}
        for key in ("max_turns", "max_tool_calls", "max_context_chars", "max_candidate_files", "timeout_seconds"):
            value = payload.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise LocateConfigError(f"locate.{key} 必须是正整数")
            values[key] = value
        return cls(provider=provider, source=source, **values)

    @property
    def tools(self) -> tuple[str, ...]:
        return READ_TOOLS

    @property
    def writable(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "writable": False,
            "tools": list(self.tools),
            "max_turns": self.max_turns,
            "max_tool_calls": self.max_tool_calls,
            "max_context_chars": self.max_context_chars,
            "max_candidate_files": self.max_candidate_files,
            "timeout_seconds": self.timeout_seconds,
            "source": self.source,
        }


__all__ = ["DEFAULT_PATH", "ENV_PREFIX", "LocateConfig", "LocateConfigError", "READ_TOOLS"]
