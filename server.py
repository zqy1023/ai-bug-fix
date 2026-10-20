"""服务入口。

    python server.py
"""

import logging

import uvicorn
from fastapi.middleware.cors import CORSMiddleware
from yq_common.fastapi_utils.fastapi_utils import FastAPIUtils

from app.infrastructure.config import load_config
from app.infrastructure.filesystem.workspace_gc import prune_workspaces
from workbench.api import SERVICE_VERSION, router

logger = logging.getLogger(__name__)
config = load_config()

# 工作区是可重建缓存；启动时回收终态执行和超时临时克隆，避免磁盘无限增长。
if config.workspace_gc_enabled:
    try:
        prune_workspaces(
            config.runtime_dir / "workspaces",
            config.runtime_dir / "runs",
            retention_days=config.workspace_retention_days,
            delivery_ttl_hours=config.delivery_workspace_ttl_hours,
        )
    except Exception:
        logger.exception("启动时回收过期工作区失败")
app = FastAPIUtils.setup_fastapi_server("py.ai.bug.fixer")
app.title = "AI Repair Workbench API"
app.version = SERVICE_VERSION
app.description = "Run/task/step/artifact observability and human approval API."
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(config.workbench_cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(router)

if __name__ == "__main__":
    uvicorn.run(
        app=app,
        host=config.workbench_host,
        port=config.workbench_api_port,
        access_log=True,
    )