"""采集验证进程的解释器选择及实际环境回执。"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import sys
import logging
import tempfile
import os
import hashlib


def prepare_runtime_logging() -> None:
    """依赖日志留在受控进程 stderr，避免默认写用户目录。"""
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)


def initialize_framework_runtime() -> None:
    """按采集服务入口顺序初始化日志和进程内指标，不启动 HTTP/订阅服务。"""
    if not (Path.cwd() / "server.py").is_file():
        return
    state_root = os.environ.get("AI_REPAIR_RUNTIME_STATE_DIR")
    if state_root:
        import nacos.client
        snapshot = Path(state_root) / "nacos-snapshot"
        snapshot.mkdir(parents=True, exist_ok=True)
        nacos.client.DEFAULTS["SNAPSHOT_BASE"] = str(snapshot)
    from yq_common.log_utils import LogUtils
    from yq_common.prometheus_utils.prometheus_utils import PrometheusUtils

    if sys.platform == "win32" or sys.platform == "darwin":
        (Path.cwd() / "logs").mkdir(parents=True, exist_ok=True)
    LogUtils.initialize("gather.py.track")
    PrometheusUtils.initialize(multi_process=False)


def runtime_configuration_receipt() -> list[dict[str, str]]:
    """只记录本次运行配置快照的身份，不输出配置内容或凭据。"""
    configured = os.environ.get("AI_REPAIR_RUNTIME_STATE_DIR")
    if not configured:
        return []
    root = Path(configured) / "nacos-snapshot"
    return [{"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in sorted(root.rglob("*")) if path.is_file()]


def runtime_python() -> str:
    from config.nacos import nacos_get

    configured = nacos_get("AI_REPAIR_RUNTIME_PYTHON", "").strip()
    if not configured:
        return sys.executable
    target = Path(configured).expanduser()
    if not target.is_absolute() or not target.is_file():
        raise ValueError("AI_REPAIR_RUNTIME_PYTHON 必须指向存在的绝对解释器路径")
    return str(target.resolve())


def runtime_temporary_directory(prefix: str):
    """采集子进程须能写入隔离目录，避免用户 Temp 的跨进程写入权限差异。"""
    from infrastructure.llm.config import load_config

    root = load_config().runtime_dir / "execution-temp"
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=root)


def environment_receipt() -> dict:
    packages = {}
    for name in ("yq_common", "pydantic", "anyio", "httpx", "lxml", "beautifulsoup4", "parsel", "curl-cffi"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {"executable": sys.executable, "python": sys.version, "packages": packages}
