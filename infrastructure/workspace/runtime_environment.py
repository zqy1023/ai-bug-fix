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


def sanitize_ssl_environment() -> list[str]:
    """清掉会让 SSL 初始化失败的机器级环境变量，返回被清理的键名。

    ``SSLKEYLOGFILE`` 是 NSS 的调试开关，机器上常被指到盘符根目录
    （例如 ``D:\\nss_ssl_sfagent.log``）。``ssl.create_default_context()`` 会直接
    打开该文件写密钥日志，路径不可写时抛 PermissionError——而这个调用发生在
    **import 阶段**（aiohttp 导入即触发），于是整个采集框架导入失败，
    所有真实验证都变成 "框架没加载" 的假失败。

    只在验证子进程里清理，不改机器级环境变量。
    """
    removed: list[str] = []
    value = os.environ.get("SSLKEYLOGFILE", "")
    if value:
        os.environ.pop("SSLKEYLOGFILE", None)
        removed.append("SSLKEYLOGFILE")
    return removed


def initialize_framework_runtime() -> None:
    """按采集服务入口顺序初始化日志和进程内指标，不启动 HTTP/订阅服务。"""
    # 必须在任何框架 import 之前清理：aiohttp 在 import 期就创建 SSL context，
    # 路径不可写时整框架导入失败。校验副本里没有 server.py，所以这一步不能
    # 放在"是采集服务目录"的判断之后——否则真实验证永远是假失败。
    sanitize_ssl_environment()
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
    # 框架日志写不出去（目录只读、磁盘满、被占用）不该让整轮验证崩掉：
    # 日志只是副产物，采集结果才是断言依据。
    try:
        LogUtils.initialize("gather.py.track")
    except OSError as error:
        logging.getLogger(__name__).warning("框架日志初始化失败，改用 stderr 输出: %s", error)
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
