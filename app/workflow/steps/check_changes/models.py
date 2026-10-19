"""确定性门禁的输入输出契约。"""

from __future__ import annotations

from typing import Any

from app.workflow.spec import FrozenModel
from pydantic import Field


class CheckInput(FrozenModel):
    """check_changes 的输入。"""

    # carrier：运输商标识。
    carrier: str
    # ai_workspace_path：隔离 AI 工作区路径。
    ai_workspace_path: str
    # changed_files：变更文件清单。
    changed_files: list[str] = Field(default_factory=list)
    # baseline_manifest：基线文件哈希清单。
    baseline_manifest: dict[str, str] = Field(default_factory=dict)
    # baseline_dir：只读基线目录。
    baseline_dir: str = ""


class FileEvidence(FrozenModel):
    """单个文件的门禁证据。

    早先 ``CheckOutput`` 只保留文件名，"改了哪个文件"看得见、"改了什么"看不见——
    逐文件证据在 ``validate_changed_files`` 里已经算出来，却在落盘时被丢弃。现原样保留。
    """

    # path：文件相对路径。
    path: str
    # in_scope：是否位于授权范围。
    in_scope: bool = True
    # exists：文件是否存在。
    exists: bool = True
    # status：文件差异状态。
    status: str = "unchanged"
    # added：新增行数。
    added: int = 0
    # removed：删除行数。
    removed: int = 0
    # size：文件大小（字节）。
    size: int = 0
    # sha256：文件内容 SHA-256。
    sha256: str = ""
    # nlink：硬链接数量。
    nlink: int = 0
    # secret_hits：敏感信息命中数。
    secret_hits: int = 0
    # secret_samples：脱敏后的敏感信息样例。
    secret_samples: list[dict[str, Any]] = Field(default_factory=list)
    # ast：Python 语法检查结论。
    ast: str = "skipped"
    # dangerous_calls：危险调用清单。
    dangerous_calls: list[str] = Field(default_factory=list)
    # problems：文件级问题清单。
    problems: list[str] = Field(default_factory=list)


class CheckOutput(FrozenModel):
    """check_changes 的输出。

    除通过与否外，必须带上**可核对的差异证据**：做了哪些检查、逐文件结论、声明与
    真实差异的对照。只有"通过"和文件名清单的门禁无法回答"模型到底改了什么"。
    完整的逐行差异（``diff``）是审查与下一轮修复的输入，体量大，落
    ``artifacts/diff.json`` 按引用读，不进本模型。
    """

    # passed：是否通过当前门禁。
    passed: bool
    # reason：处理原因。
    reason: str = ""
    # failure_count：失败项数量。
    failure_count: int = 0
    # changed_files：变更文件清单。
    changed_files: list[str] = Field(default_factory=list)
    # checks：执行过的检查项。
    checks: list[str] = Field(default_factory=list)
    # scope：变更范围。
    scope: dict[str, Any] = Field(default_factory=dict)
    # claim：模型声明与实际改动的对账结果。
    claim: dict[str, Any] = Field(default_factory=dict)
    # totals：差异统计。
    totals: dict[str, int] = Field(default_factory=dict)
    # files：逐文件证据。
    files: list[FileEvidence] = Field(default_factory=list)
    # failures：失败明细。
    failures: list[dict[str, str]] = Field(default_factory=list)


__all__ = ["CheckInput", "CheckOutput", "FileEvidence"]
