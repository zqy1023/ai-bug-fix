"""系统主入口文件与统一 Web 服务。

启动方式：
    python server.py                      # 执行一次，推进到审批或终态
    python server.py --web                # 同时启动后端 API
    python server.py --frontend           # 同时启动前后端
    python server.py --serve-only         # 仅启动 API
"""

from __future__ import annotations

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

from infrastructure.llm.config import load_config
from infrastructure.workspace.workspace_gc import start_workspace_collector
from config.nacos import nacos_get
from config.settings import settings
from server.routes import SERVICE_VERSION, router

logger = logging.getLogger(__name__)
config = load_config()

PROJECT_NAME = settings.project_name

_LOOPBACK_HOSTS = set(settings.loopback_hosts)


def _ensure_writable_home() -> None:
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
    if platform.system().lower() in {"windows", "darwin"}:
        return
    try:
        (Path(settings.linux_log_base_dir) / project_name).mkdir(parents=True, exist_ok=True)
    except OSError as error:
        logger.warning("无法创建 %s/%s：%s", settings.linux_log_base_dir, project_name, error)


def _minimal_app() -> FastAPI:
    """框架初始化失败时的最小应用：保留异常处理与路由挂载能力。"""
    from fastapi.responses import JSONResponse

    app = FastAPI()

    @app.exception_handler(Exception)
    async def _unhandled(request, exc):  # noqa: ANN001
        logger.exception("请求处理异常 url:%s", getattr(request, "url", ""))
        return JSONResponse(
            status_code=500, content={"message": "Internal Server Error"}
        )

    return app


def _build_app() -> FastAPI:
    _ensure_writable_home()
    _prepare_log_dir(PROJECT_NAME)
    try:
        return FastAPIUtils.setup_fastapi_server(PROJECT_NAME)
    except Exception:
        logger.exception("框架初始化失败，降级为最小应用")
        return _minimal_app()


def _secure_bind_host(host: str) -> str:
    token = nacos_get("WORKBENCH_API_TOKEN", "").strip()
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
    previous = _original_lifespan(app)
    async with previous:
        collector = start_workspace_collector(config) if config.workspace_gc_enabled else None
        try:
            yield
        finally:
            if collector is not None:
                stop, thread = collector
                stop.set()
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


def _arguments(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="执行一次修复审批流，可同时启动工作台")
    parser.add_argument("--web", action="store_true", help="同时启动后端 API，供人工审批")
    parser.add_argument("--frontend", action="store_true", help="同时启动前端和后端")
    parser.add_argument("--serve-only", action="store_true", help="仅启动服务，不新建流程")
    parser.add_argument("--run-id", help="本次新运行的唯一 ID，不用于恢复已有运行")
    args = parser.parse_args(argv)
    if args.serve_only and args.run_id:
        parser.error("--serve-only 不能与 --run-id 同时使用")
    return args


def _start_once(run_id=None):
    from workflow.engine.graph import prepare_run
    from server.lifecycle import _run_workflow_background_v1

    resolved, _, _ = prepare_run(run_id, overrides={}, queued=True)
    print(f"run_id={resolved}，流程将执行到人工审批或终态", flush=True)
    _run_workflow_background_v1(resolved, {})
    return resolved


def main(argv=None) -> int:
    import shutil
    import subprocess
    import threading

    args = _arguments(argv)
    frontend = None
    worker = None
    startup_failed = threading.Event()
    if args.frontend:
        node = shutil.which("node")
        vite = Path(__file__).resolve().parent / "frontend/node_modules/vite/bin/vite.js"
        if not node or not vite.is_file():
            raise RuntimeError("前端依赖未安装，请先在 frontend 执行 npm install")
        frontend_env = dict(os.environ)
        api_host = config.workbench_host
        if api_host in {"0.0.0.0", "::"}:
            api_host = "127.0.0.1"
        frontend_env["WORKBENCH_API_ORIGIN"] = f"http://{api_host}:{config.workbench_api_port}"
        frontend = subprocess.Popen(
            [node, str(vite), "--host", "127.0.0.1"], cwd=str(vite.parents[3]), env=frontend_env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    try:
        if args.web or args.frontend or args.serve_only:
            server = uvicorn.Server(uvicorn.Config(
                app=app, host=_secure_bind_host(config.workbench_host),
                port=config.workbench_api_port, access_log=True,
            ))
            if not args.serve_only:
                def launch():
                    import time
                    while not server.started:
                        if server.should_exit:
                            return
                        time.sleep(0.1)
                    try:
                        if frontend is not None and frontend.poll() is not None:
                            raise RuntimeError("前端启动失败，未启动新流程")
                        _start_once(args.run_id)
                    except Exception:
                        startup_failed.set()
                        logger.exception("启动审批流失败")
                        server.should_exit = True
                worker = threading.Thread(target=launch, name="startup-workflow", daemon=True)
                worker.start()
            server.run()
            return 1 if startup_failed.is_set() else 0
        resolved = _start_once(args.run_id)
        from infrastructure.storage.lifecycle import LifecycleStore
        status = LifecycleStore(config.runtime_dir).read_run(resolved).get("status")
        print(f"run_id={resolved} status={status}；待审批流程可用 --serve-only 打开工作台继续", flush=True)
        return 1 if status == "failed" else 0
    finally:
        if frontend is not None:
            frontend.terminate()
            try:
                frontend.wait(timeout=5)
            except subprocess.TimeoutExpired:
                frontend.kill()
                frontend.wait()


if __name__ == "__main__":
    raise SystemExit(main())
