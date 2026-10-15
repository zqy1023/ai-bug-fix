"""AI 异常日志自动修复工作流。

整图入口为 ``app.entrypoint.run_workflow``；编排装配在
``app.workflow.wiring.build_graph``。步骤和 Agent 可从各自包的 ``__main__`` 单独调试。
"""

from __future__ import annotations

__all__: list[str] = []
