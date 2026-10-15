"""工作流结构化错误。

错误类型只描述失败类别和恢复边界，不携带密钥、完整单号或隐藏思考。
"""

from __future__ import annotations


class WorkflowError(RuntimeError):
    """工作流系统错误基类。"""


class InputValidationError(WorkflowError):
    """输入字段或状态无效，不重试。"""


class ExternalServiceError(WorkflowError):
    """外部服务调用失败，可按 operation key 有限重试。"""


class AgentOutputError(WorkflowError):
    """Agent 输出为空、截断或结构非法，不无限重试。"""


class PermissionDeniedError(WorkflowError):
    """步骤或适配器超出授权范围，必须转人工。"""


class VerificationFailedError(WorkflowError):
    """确定性或独立验证失败，回到修复轮次。"""


class RegressionFailedError(WorkflowError):
    """准生产回归失败，禁止进入审查和交付。"""


class StepFailedError(WorkflowError):
    """步骤执行失败，由流水线决定回流还是终止。"""

    def __init__(self, step_name: str, reason: str) -> None:
        """记录失败步骤和原因。"""
        super().__init__(f"{step_name}: {reason}")
        self.step_name = step_name
        self.reason = reason


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
        """保存失败状态和已创建分支上下文。"""
        super().__init__(message)
        self.status = status
        self.branch_workspace_path = branch_workspace_path
        self.commit = commit


class StepFailed(RuntimeError):
    """步骤业务失败。``result`` 由工作流路由解释，不在步骤中决定下一步。

    ``code`` 是可选的结构化错误码，取值由应用层的固定词汇表约束；产物读写与
    报告写入这类故障带上它，记录里才能给出可定位的原因。
    """

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
