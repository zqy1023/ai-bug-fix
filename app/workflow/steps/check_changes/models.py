"""确定性门禁的输入输出契约。"""

from __future__ import annotations

from typing import Any

from app.workflow.spec import FrozenModel
from pydantic import Field


class CheckInput(FrozenModel):
    """check_changes 的输入。``authorized_files`` 是 apply_plan 授权后放行的额外范围。"""

    carrier: str
    ai_workspace_path: str
    changed_files: list[str] = Field(default_factory=list)
    baseline_manifest: dict[str, str] = Field(default_factory=dict)
    baseline_dir: str = ""
    authorized_files: list[str] = Field(default_factory=list)


class FileEvidence(FrozenModel):
    """单个文件的门禁证据。

    早先 ``CheckOutput`` 只保留文件名，"改了哪个文件"看得见、"改了什么"看不见——
    逐文件证据在 ``validate_changed_files`` 里已经算出来，却在落盘时被丢弃。现原样保留。
    """

    path: str
    in_scope: bool = True
    exists: bool = True
    status: str = "unchanged"
    added: int = 0
    removed: int = 0
    size: int = 0
    sha256: str = ""
    nlink: int = 0
    secret_hits: int = 0
    secret_samples: list[dict[str, Any]] = Field(default_factory=list)
    ast: str = "skipped"
    dangerous_calls: list[str] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)


class CheckOutput(FrozenModel):
    """check_changes 的输出。

    除通过与否外，必须带上**可核对的差异证据**：做了哪些检查、逐文件结论、声明与
    真实差异的对照。只有"通过"和文件名清单的门禁无法回答"模型到底改了什么"。
    完整的逐行差异（``diff``）是审查与下一轮修复的输入，体量大，落
    ``artifacts/diff.json`` 按引用读，不进本模型。
    """

    passed: bool
    reason: str = ""
    failure_count: int = 0
    changed_files: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    scope: dict[str, Any] = Field(default_factory=dict)
    claim: dict[str, Any] = Field(default_factory=dict)
    totals: dict[str, int] = Field(default_factory=dict)
    files: list[FileEvidence] = Field(default_factory=list)
    failures: list[dict[str, str]] = Field(default_factory=list)


__all__ = ["CheckInput", "CheckOutput", "FileEvidence"]