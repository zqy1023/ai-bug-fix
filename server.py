"""服务入口。

    python server.py
"""

import logging
import os
import platform
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from yq_common.fastapi_utils.fastapi_utils import FastAPIUtils

from app.infrastructure.config import load_config
from app.infrastructure.filesystem.workspace_gc import start_workspace_collector
from workbench.api import SERVICE_VERSION, router

logger = logging.getLogger(__name__)
config = load_config()

PROJECT_NAME = "py.ai.bug.fixer"

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _ensure_writable_home() -> None:
    """HOME 必须可写，否则 Claude Agent SDK 在运行中途以怪异错误失败。

    SDK 要在 ``~/.claude`` 下落会话与配置。容器里 compose 用
    ``APP_UID/APP_GID`` 覆盖运行用户，而镜像内 ``/home/app`` 的属主是固定的
    10001；一旦改了 ``APP_UID``，$HOME 就不可写。启动期自检一次，不可写就把
    HOME 落到运行目录下的 home——运行目录本来就要求对 APP_UID 可写。
    """
    home = Path(os.path.expanduser("~"))
    if home.is_dir() and os.access(home, os.W_OK):
        return
    fallback = Path(config.runtime_dir) / "home"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print(f"警告：HOME {home} 不可写，且 {fallback} 创建失败：{error}", flush=True)
        logger.warning("HOME %s 不可写，且 %s 创建失败：%s", home, fallback, error)
        return
    os.environ["HOME"] = str(fallback)
    os.environ["USERPROFILE"] = str(fallback)
    print(f"警告：HOME {home} 不可写，已改用 {fallback}", flush=True)
    logger.warning("HOME %s 不可写，已改用 %s", home, fallback)


def _prepare_log_dir(project_name: str) -> None:
    """把 yq_common 的日志目录先建好。

    该库在 Linux 下把日志固定写到 ``/var/log/yqtrack/<project>``，并且用裸
    ``except`` 吞掉 ``os.makedirs`` 的 PermissionError，随后 RotatingFileHandler
    因目录不存在抛 FileNotFoundError——异常在 import 期逃出，进程直接退出，
    容器陷入无限重启。建不出来时不阻断启动，由下面的兜底分支接管。
    """
    if platform.system().lower() in {"windows", "darwin"}:
        return
    try:
        (Path("/var/log/yqtrack") / project_name).mkdir(parents=True, exist_ok=True)
    except OSError as error:
        logger.warning("无法创建 /var/log/yqtrack/%s：%s", project_name, error)


def _build_app() -> FastAPI:
    """构建 FastAPI 应用。

    框架的 setup_fastapi_server 会顺带初始化文件日志；日志目录不可写时它会
    直接崩在 import 期，容器因此永远起不来。这里保留框架的标准装配，只在它
    失败时降级为最小可用应用（日志走 stderr）：宁可少一个日志文件，也不能
    让整个工作台不可用。
    """
    _ensure_writable_home()
    _prepare_log_dir(PROJECT_NAME)
    try:
        return FastAPIUtils.setup_fastapi_server(PROJECT_NAME)
    except Exception:
        logger.exception("框架初始化失败，降级为最小应用")
        return _minimal_app()


def _minimal_app() -> FastAPI:
    """框架初始化失败时的最小应用：保留异常处理与路由挂载能力。"""
    from fastapi.responses import JSONResponse

    app = FastAPI()

    @app.exception_handler(Exception)
    async def _unhandled(request, exc):  # noqa: ANN001 - 框架回调签名
        logger.exception("请求处理异常 url:%s", getattr(request, "url", ""))
        return JSONResponse(
            status_code=500, content={"message": "Internal Server Error"}
        )

    return app


def _secure_bind_host(host: str) -> str:
    """未配置 WORKBENCH_API_TOKEN 时收敛绑定地址，避免未鉴权 API 暴露到非回环网络。

    _authorize 在未配置 token 时 fail-open（本地开发依赖该行为），因此启动阶段
    必须兜底：token 缺失且监听地址不是回环地址时，回落为 127.0.0.1 并打印显著
    warning；仅当 WORKBENCH_ALLOW_INSECURE 显式豁免时保留原地址。
    """
    token = os.getenv("WORKBENCH_API_TOKEN", "").strip()
    if token or host in _LOOPBACK_HOSTS or config.workbench_allow_insecure:
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


app = _build_app()
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