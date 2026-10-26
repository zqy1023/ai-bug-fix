"""服务入口。

    python server.py
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from yq_common.fastapi_utils.fastapi_utils import FastAPIUtils

from app.infrastructure.config import load_config
from app.infrastructure.filesystem.workspace_gc import start_workspace_collector
from config.settings import settings
from workbench.api import SERVICE_VERSION, router

logger = logging.getLogger(__name__)
config = load_config()

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _secure_bind_host(host: str) -> str:
    """未配置 WORKBENCH_API_TOKEN 时收敛绑定地址，避免未鉴权 API 暴露到非回环网络。

    _authorize 在未配置 token 时 fail-open（本地开发依赖该行为），因此启动阶段
    必须兜底：token 缺失且监听地址不是回环地址时，回落为 127.0.0.1 并打印显著
    warning；仅当 WORKBENCH_ALLOW_INSECURE 显式豁免时保留原地址。
    """
    token = os.getenv("WORKBENCH_API_TOKEN", "").strip()
    if token or host in _LOOPBACK_HOSTS or settings.workbench_allow_insecure:
        return host
    warning = (
        "安全警告：未设置 WORKBENCH_API_TOKEN，工作台 API 对所有请求无鉴权放行。\n"
        f"已把监听地址从 {host} 回落为 127.0.0.1；如确需对外监听，"
        "请设置 WORKBENCH_API_TOKEN，或显式设置 WORKBENCH_ALLOW_INSECURE=1 豁免。"
    )
    print(warning, flush=True)
    logger.warning("%s", warning)
    return "127.0.0.1"

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
        host=_secure_bind_host(config.workbench_host),
        port=config.workbench_api_port,
        access_log=True,
    )