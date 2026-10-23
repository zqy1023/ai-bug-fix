"""服务入口。

    python server.py
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from yq_common.fastapi_utils.fastapi_utils import FastAPIUtils

from app.infrastructure.config import load_config
from app.infrastructure.filesystem.workspace_gc import start_workspace_collector
from workbench.api import SERVICE_VERSION, router

logger = logging.getLogger(__name__)
config = load_config()

@asynccontextmanager
async def workspace_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """接入框架已有 lifespan；启动回收、定时回收及退出停止共用生命周期。"""
    previous = _original_lifespan(app)
    async with previous:
        collector = start_workspace_collector(config) if config.workspace_gc_enabled else None
        try:
            yield
        finally:
            if collector is not None:
                stop, thread = collector
                stop.set()
                # 删除工作区可能耗时；不在事件循环中等待磁盘操作。
                import asyncio
                await asyncio.to_thread(thread.join, 5)


app = FastAPIUtils.setup_fastapi_server("py.ai.bug.fixer")
_original_lifespan = app.router.lifespan_context
app.router.lifespan_context = workspace_lifespan
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