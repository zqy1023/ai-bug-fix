"""加载唯一公共 Prompt 和按职责划分的 Agent Prompt。"""

from __future__ import annotations

import re
from pathlib import Path

from core.contracts import AgentRole
from infrastructure.llm.models import PromptDocument


class PromptTemplateError(ValueError):
    """Prompt 文件缺失、版本头错误或角色未注册。"""


_PROMPT_DIRECTORY = Path(__file__).parent / "templates"
_VERSION_LINE = re.compile(r"\A<!-- prompt-version: ([0-9]+\.[0-9]+\.[0-9]+) -->\r?\n")
_ROLE_FILES: dict[AgentRole, str] = {
    "unified_fixer": "unified_fixer.md",
    "review": "review.md",
}


class PromptLoader:
    """读取公共及角色 Prompt；每次调用均以磁盘上的当前版本为准。"""

    def __init__(self, directory: Path = _PROMPT_DIRECTORY) -> None:
        self._directory = directory

    def load(self, role: AgentRole) -> tuple[PromptDocument, PromptDocument]:
        """返回唯一公共文档和该职责的 Prompt。"""
        filename = _ROLE_FILES.get(role)
        if filename is None:
            raise PromptTemplateError(f"未注册的 Agent Prompt 角色: {role}")
        return self._read(self._directory / "common.md"), self._read(self._directory / filename)

    @staticmethod
    def _read(path: Path) -> PromptDocument:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            raise PromptTemplateError(f"Prompt 文件读取失败: {path}") from error
        match = _VERSION_LINE.match(text)
        if match is None:
            raise PromptTemplateError(f"Prompt 缺少合法版本头: {path}")
        body = text[match.end() :].strip()
        if not body:
            raise PromptTemplateError(f"Prompt 正文为空: {path}")
        return PromptDocument(version=match.group(1), body=body)


__all__ = ["PromptLoader", "PromptTemplateError"]
