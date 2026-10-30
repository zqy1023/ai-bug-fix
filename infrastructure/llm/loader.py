"""加载同源公共规则、领域片段和按职责划分的 Agent Prompt。"""

from __future__ import annotations

import re
from collections.abc import Sequence
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

    def load(
        self,
        role: AgentRole,
        rule_ids: Sequence[str] | None = None,
        role_variant: str = "",
    ) -> tuple[PromptDocument, PromptDocument]:
        """返回公共规则文档和该职责的 Prompt。

        ``rule_ids`` 为 None 时使用 core 与当前全部领域片段；
        否则拼接 core 与指定片段。``role_variant`` 选择角色提示词变体
        （例如 ``small`` → ``unified_fixer.small.md``），用于证据充分的小任务。
        """
        filename = _ROLE_FILES.get(role)
        if filename is None:
            raise PromptTemplateError(f"未注册的 Agent Prompt 角色: {role}")
        if role_variant:
            stem, _, suffix = filename.rpartition(".")
            filename = f"{stem}.{role_variant}.{suffix}"
        role_document = self._read(self._directory / filename)
        if rule_ids is None:
            from infrastructure.llm.rules import ALL_RULES
            rule_ids = ALL_RULES
        bodies: list[str] = []
        versions: list[str] = []
        for rule_id in ("core", *dict.fromkeys(rule_ids)):
            document = self._read(self._rule_path(rule_id))
            bodies.append(document.body)
            versions.append(f"{rule_id}/{document.version}")
        common = PromptDocument(version="+".join(versions), body="\n\n".join(bodies))
        return common, role_document

    def _rule_path(self, rule_id: str) -> Path:
        if rule_id == "core":
            return self._directory / "core.md"
        return self._directory / "rules" / f"{rule_id}.md"

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
