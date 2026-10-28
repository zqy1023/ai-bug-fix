"""工作流结构化错误与异常定义。"""

from __future__ import annotations


class WorkflowError(RuntimeError):
    """工作流系统错误基类。"""


class DeliveryError(WorkflowError):
    """修复分支交付失败，并携带人工接管所需上下文。"""

    def __init__(
        self,
        message: str,
        *,
        status: str = "MANUAL",
        branch_workspace_path: str | None = None,
        commit: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.branch_workspace_path = branch_workspace_path
        self.commit = commit


class StepFailed(RuntimeError):
    """步骤业务失败。result 由工作流路由解释，不在步骤中决定下一步。"""

    def __init__(
        self,
        message: str,
        output: dict[str, object] | None = None,
        *,
        result: str = "failed",
        code: str = "",
    ) -> None:
        super().__init__(message)
        self.output = dict(output or {})
        self.result = result
        self.code = code


class PolicyError(WorkflowError):
    """策略或越权违规异常。"""


__all__ = ["WorkflowError", "DeliveryError", "StepFailed", "PolicyError"]
