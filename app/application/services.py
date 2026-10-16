"""运行时服务契约。

步骤通过 ``WorkflowServices`` 拿到配置化的外部协作者（工作区、交付、日志源）。真实实现只在 ``app.workflow.wiring`` 中装配，步骤本身不创建任何外部客户端，
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
    from app.application.ports import ScopePolicy


@dataclass
class WorkflowServices:
    """一次执行的外部依赖集合。"""

    # source_repo：本轮使用的源 Git 仓库路径。
    source_repo: Path | None = None
    # project_subdir：仓库中业务项目的相对路径。
    project_subdir: Path | None = None
    # verify_commands：在隔离工作区执行的固定验证命令列表。
    verify_commands: list[list[str]] = field(default_factory=list)
    # git_push：是否允许交付阶段推送修复分支。
    git_push: bool = False
    # workspaces：工作区快照、隔离、回归目录的管理器。
    workspaces: WorkspaceManager | None = None
    # delivery：创建修复分支、提交和可选推送的适配器。
    delivery: GitBranchDelivery | None = None
    # log_source：读取真实 ES 异常日志的适配器。
    log_source: ESLogAdapter | None = None
    # ignore_errors：在归一化阶段排除的错误消息清单。
    ignore_errors: list[str] = field(default_factory=list)
    # scope_policy：判断变更是否超出目标 carrier 授权范围的策略。
    scope_policy: ScopePolicy | None = None
    # metadata：注入验证器和其他基础设施适配器的扩展依赖。
    metadata: dict[str, Any] = field(default_factory=dict)

    def close(self) -> None:
        """外部资源由适配器自己持有，这里没有共享通道要关。"""
