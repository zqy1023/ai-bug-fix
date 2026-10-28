"""生产工作流工作台 API 门面。

业务接口路由已按架构蓝图拆分重构至 server.routes (runs, tasks, approvals, artifacts)。
本模块保留作为向下兼容门面，导出统一 router 及全部公共模型与端点。
"""

from __future__ import annotations

from server.routes import (
    SERVICE_NAME,
    SERVICE_VERSION,
    approvals_router,
    artifacts_router,
    router as unified_router,
    runs_router,
    tasks_router,
)
from server.routes.approvals import *
from server.routes.artifacts import *
from server.routes.common import *
from server.routes.runs import *
from server.routes.tasks import *

# 必须使用统一聚合后的主 router
router = unified_router

__all__ = [name for name in dir() if not name.startswith("__")]
