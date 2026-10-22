"""证据准备步骤的输入输出契约。"""

from __future__ import annotations

from typing import Any, Literal

from app.workflow.spec import FrozenModel
from pydantic import Field


class EvidenceInput(FrozenModel):
    """模型调用前的确定性任务与源码证据输入。"""

    # task_id：字段说明见该模型文档与工作流契约。
    task_id: str
    # carrier：字段说明见该模型文档与工作流契约。
    carrier: str
    # biztype：字段说明见该模型文档与工作流契约。
    biztype: str
    # service_name：字段说明见该模型文档与工作流契约。
    service_name: str
    # problem：字段说明见该模型文档与工作流契约。
    problem: dict[str, Any] = Field(default_factory=dict)
    # workspace_path：只允许访问已复制的隔离工作区。
    workspace_path: str
    # framework：字段说明见该模型文档与工作流契约。
    framework: Literal["gathers", "spiders"]


class EvidenceOutput(FrozenModel):
    """候选文件、异常分类与旧框架门禁，不包含文件全文。

    ``legacy_only`` 是确定性判定结果：目标 carrier 只有旧框架 ``spiders/``
    实现、没有新框架入口 ``gathers/<carrier>/main.py``。新旧框架均允许修复当前
    carrier；此字段只说明实际入口，不代表必须转人工。
    """

    # carrier_dir：字段说明见该模型文档与工作流契约。
    carrier_dir: str = ""
    # framework：字段说明见该模型文档与工作流契约。
    framework: Literal["gathers", "spiders"] = "gathers"
    # candidates：字段说明见该模型文档与工作流契约。
    candidates: list[str] = Field(default_factory=list)
    # symbols：字段说明见该模型文档与工作流契约。
    symbols: dict[str, list[str]] = Field(default_factory=dict)
    # skipped_large_files：字段说明见该模型文档与工作流契约。
    skipped_large_files: list[str] = Field(default_factory=list)
    # syntax_errors：字段说明见该模型文档与工作流契约。
    syntax_errors: dict[str, str] = Field(default_factory=dict)
    # category：字段说明见该模型文档与工作流契约。
    category: str = "unknown"
    # extracted：字段说明见该模型文档与工作流契约。
    extracted: dict[str, str] = Field(default_factory=dict)
    # legacy_only：字段说明见该模型文档与工作流契约。
    legacy_only: bool = False
    # 新入口是否转发旧 spiders 实现；用于补齐同 carrier 的真实候选代码。
    legacy_dependency: bool = False
    # legacy_reason：字段说明见该模型文档与工作流契约。
    legacy_reason: str = ""
__all__ = ["EvidenceInput", "EvidenceOutput"]
