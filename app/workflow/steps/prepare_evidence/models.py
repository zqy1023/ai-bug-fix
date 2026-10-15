"""证据准备步骤的输入输出契约。"""

from __future__ import annotations

from typing import Any, Literal

from app.workflow.spec import FrozenModel
from pydantic import Field


class EvidenceInput(FrozenModel):
    """模型调用前的确定性任务与源码证据输入。"""

    task_id: str
    carrier: str
    biztype: str
    service_name: str
    problem: dict[str, Any] = Field(default_factory=dict)
    source_project_path: str
    framework: Literal["gathers", "spiders"]


class EvidenceOutput(FrozenModel):
    """候选文件、异常分类与旧框架门禁，不包含文件全文。

    ``legacy_only`` 是确定性判定结果：目标 carrier 只有旧框架 ``spiders/``
    实现、没有新框架入口 ``gathers/<carrier>/main.py``。这种任务允许 Locate
    只读分析，分析后转人工，不进入 AI 修复。
    """

    carrier_dir: str = ""
    framework: Literal["gathers", "spiders"] = "gathers"
    candidates: list[str] = Field(default_factory=list)
    symbols: dict[str, list[str]] = Field(default_factory=dict)
    skipped_large_files: list[str] = Field(default_factory=list)
    syntax_errors: dict[str, str] = Field(default_factory=dict)
    category: str = "unknown"
    extracted: dict[str, str] = Field(default_factory=dict)
    legacy_only: bool = False
    legacy_reason: str = ""


__all__ = ["EvidenceInput", "EvidenceOutput"]
