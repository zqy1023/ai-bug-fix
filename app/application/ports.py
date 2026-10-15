"""内核端口。

步骤只依赖这些协议，不直接 import 适配器实现。真实实现装配在
:mod:`app.workflow.wiring`，单步调试可以塞假实现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.workflow.spec import FrozenModel

if TYPE_CHECKING:
    from pathlib import Path

    from app.application.plans import ChangePlan


class ScopeDecision(FrozenModel):
    """一次落盘范围判定的结论。``roots`` 是工作区相对允许目录，``.`` 表示整仓。"""

    allowed: bool
    tier: str = "carrier"
    authorized: bool = False
    reason: str = ""
    roots: tuple[str, ...] = ()


@runtime_checkable
class ScopePolicy(Protocol):
    """影响面授权策略：carrier 默认放行，project 需人工授权。"""

    def decide(self, *, plan: "ChangePlan", decision: str, carrier: str) -> ScopeDecision:
        """按方案影响面和人工决定给出落盘范围。"""
        ...


@runtime_checkable
class WorkspacePort(Protocol):
    """隔离工作区操作。"""

    def locate_carrier_roots(self, workspace: "str | Path", carrier: str) -> list[str]:
        """返回 carrier 在工作区内的相对目录。"""
        ...

    def write(self, workspace: "str | Path", relative: str, content: str) -> Path:
        """在工作区内写文件，返回绝对路径。"""
        ...

    def delete(self, workspace: "str | Path", relative: str) -> bool:
        """删除工作区内文件。"""
        ...


__all__ = ["ScopeDecision", "ScopePolicy", "WorkspacePort"]