"""运行时服务契约。

步骤通过 ``WorkflowServices`` 拿到配置化的外部协作者（工作区、交付、资格、日志源）。真实实现只在 ``app.workflow.wiring`` 中装配，步骤本身不创建任何外部客户端，
缺少生产依赖时对应步骤必须失败。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.infrastructure.filesystem.workspace import WorkspaceManager
    from app.infrastructure.git.delivery import GitBranchDelivery
    from app.infrastructure.es.logs import ESLogAdapter
    from app.infrastructure.eligibility import ExecutionEligibility
    from app.application.ports import ScopePolicy


@dataclass
class WorkflowServices:
    """一次执行的外部依赖集合。"""

    source_repo: Path | None = None
    project_subdir: Path | None = None
    verify_commands: list[list[str]] = field(default_factory=list)
    git_push: bool = False
    workspaces: WorkspaceManager | None = None
    delivery: GitBranchDelivery | None = None
    eligibility: ExecutionEligibility | None = None
    log_source: ESLogAdapter | None = None
    ignore_errors: list[str] = field(default_factory=list)
    scope_policy: ScopePolicy | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def close(self) -> None:
        """外部资源由适配器自己持有，这里没有共享通道要关。"""
