"""Nacos 配置中心集成：优先读取 Nacos，降级到环境变量。

配置解析链：Nacos 配置中心 > 环境变量 / .env > config/settings.py 默认值。
Nacos 不可用时（网络异常、未安装、未启用）自动降级，不阻断系统启动。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from config.settings import parse_bool, settings

logger = logging.getLogger(__name__)

# Nacos 连接参数环境变量名（兼容 yq_common 的命名）
_SERVER_ADDR_KEYS = ("NACOS_SERVER_ADDR", "nacosServerAddr")
_NAMESPACE_KEYS = ("NACOS_NAMESPACE", "nacosNamespace")

# 默认值 (收敛至 settings)
_DEFAULT_SERVER_ADDR = settings.nacos_default_server_addr
_DEFAULT_NAMESPACE = settings.nacos_default_namespace
_DEFAULT_DATA_ID = settings.nacos_default_data_id
_DEFAULT_GROUP = settings.nacos_default_group
_CONNECT_TIMEOUT = settings.nacos_connect_timeout

# 模块级缓存
_nacos_config: dict[str, Any] | None = None
_nacos_loaded = False
_PUBLISH_LOCK = threading.Lock()


class NacosUnavailable(RuntimeError):
    """Nacos 未启用、不可达或无写权限；设置不能落本地冒充已保存。"""


def _env_first(*keys: str, default: str = "") -> str:
    """按优先级从多个环境变量名中取值。"""
    for key in keys:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return default


def _writable_log_dir() -> str:
    """为 nacos-sdk-python 选择可写的日志目录。

    SDK 默认往 ``~/logs/nacos/`` 写日志，Windows 或容器环境下可能没有写权限。
    优先用运行时目录，不行就用系统临时目录。
    """
    candidates = [
        os.getenv("AI_REPAIR_RUNTIME_DIR", "").strip(),
        str(Path(__file__).resolve().parents[2] / settings.runtime_dir_name),
    ]
    for base in candidates:
        if not base:
            continue
        log_dir = os.path.join(base, "nacos-log")
        try:
            os.makedirs(log_dir, exist_ok=True)
            probe = os.path.join(log_dir, ".probe")
            with open(probe, "w") as fh:
                fh.write("")
            os.remove(probe)
            return log_dir
        except OSError:
            continue
    fallback = os.path.join(tempfile.gettempdir(), "nacos-log")
    os.makedirs(fallback, exist_ok=True)
    return fallback


def _fetch_nacos_config() -> dict[str, Any]:
    """尝试从 Nacos 拉取配置；任何失败返回空字典并记录警告。"""
    enabled = parse_bool(os.getenv("NACOS_ENABLED"), default=settings.nacos_enabled)
    if not enabled:
        logger.debug("Nacos 配置中心已禁用 (NACOS_ENABLED=false)")
        return {}

    server_addr = _env_first(*_SERVER_ADDR_KEYS, default=_DEFAULT_SERVER_ADDR)
    namespace = _env_first(*_NAMESPACE_KEYS, default=_DEFAULT_NAMESPACE)
    data_id = os.getenv("NACOS_DATA_ID", _DEFAULT_DATA_ID).strip() or _DEFAULT_DATA_ID
    group = os.getenv("NACOS_GROUP", _DEFAULT_GROUP).strip() or _DEFAULT_GROUP

    try:
        import nacos  # noqa: F811 — 延迟导入，未安装时降级
    except ImportError:
        logger.debug("nacos-sdk-python 未安装，跳过 Nacos 配置")
        return {}

    try:
        log_dir = _writable_log_dir()
        client = nacos.NacosClient(
            server_addresses=server_addr,
            namespace=namespace,
            username=os.getenv("NACOS_USERNAME", "").strip() or None,
            password=os.getenv("NACOS_PASSWORD", "") or None,
            logDir=log_dir,
        )
        raw = client.get_config(data_id, group, timeout=_CONNECT_TIMEOUT)
        if not raw:
            logger.info("Nacos 配置为空 data_id=%s group=%s", data_id, group)
            return {}
        config = json.loads(raw)
        if not isinstance(config, dict):
            logger.warning("Nacos 配置根值不是 JSON 对象 data_id=%s", data_id)
            return {}
        logger.info(
            "Nacos 配置加载成功 data_id=%s group=%s 键数=%d",
            data_id, group, len(config),
        )
        return config
    except json.JSONDecodeError:
        logger.warning("Nacos 配置内容不是合法 JSON data_id=%s", data_id, exc_info=True)
        return {}
    except Exception:
        logger.warning(
            "Nacos 配置加载失败，回退到环境变量 server=%s data_id=%s group=%s",
            server_addr, data_id, group,
            exc_info=True,
        )
        return {}


def load_nacos_config(*, force: bool = False) -> dict[str, Any]:
    """加载并缓存 Nacos 配置。``force=True`` 强制重新拉取。"""
    global _nacos_config, _nacos_loaded
    if _nacos_loaded and not force:
        return _nacos_config or {}
    _nacos_config = _fetch_nacos_config()
    _nacos_loaded = True
    return _nacos_config or {}


def nacos_get(key: str, default: str = "") -> str:
    """优先从 Nacos 读取配置值，其次从环境变量读取。

    Nacos 配置中同时尝试原始键名、大写和小写匹配。
    值为列表或字典时转为 JSON 字符串，与 ``os.getenv`` 返回类型一致。
    """
    config = load_nacos_config()
    for candidate in dict.fromkeys((key, key.upper(), key.lower())):
        value = config.get(candidate)
        if value is not None:
            if isinstance(value, (list, dict)):
                return json.dumps(value, ensure_ascii=False)
            return str(value)
    return os.getenv(key, default)


def nacos_bool(name: str, default: bool = False) -> bool:
    """优先从 Nacos 读取布尔值，其次从环境变量读取。"""
    config = load_nacos_config()
    for candidate in dict.fromkeys((name, name.upper(), name.lower())):
        value = config.get(candidate)
        if value is not None:
            if isinstance(value, bool):
                return value
            return parse_bool(str(value), default)
    return parse_bool(os.getenv(name), default)


def nacos_target() -> dict[str, Any]:
    """当前 Nacos 目标（不向前端暴露服务地址）。"""
    enabled = parse_bool(os.getenv("NACOS_ENABLED"), default=settings.nacos_enabled)
    return {
        "enabled": enabled,
        "data_id": os.getenv("NACOS_DATA_ID", _DEFAULT_DATA_ID).strip() or _DEFAULT_DATA_ID,
        "group": os.getenv("NACOS_GROUP", _DEFAULT_GROUP).strip() or _DEFAULT_GROUP,
        "namespace": _env_first(*_NAMESPACE_KEYS, default=_DEFAULT_NAMESPACE),
    }


def publish_nacos_values(updates: Mapping[str, Any]) -> dict[str, Any]:
    """读-改-写 Nacos JSON 配置；失败抛 NacosUnavailable，绝不落本地。"""
    target = nacos_target()
    if not target["enabled"]:
        raise NacosUnavailable("Nacos 未启用，无法保存设置")
    server_addr = _env_first(*_SERVER_ADDR_KEYS, default=_DEFAULT_SERVER_ADDR)
    try:
        import nacos  # noqa: F811
    except ImportError as exc:
        raise NacosUnavailable("nacos-sdk-python 未安装，无法保存设置") from exc

    with _PUBLISH_LOCK:
        try:
            client = nacos.NacosClient(
                server_addresses=server_addr,
                namespace=target["namespace"],
                username=os.getenv("NACOS_USERNAME", "").strip() or None,
                password=os.getenv("NACOS_PASSWORD", "") or None,
                logDir=_writable_log_dir(),
            )
            raw = client.get_config(
                target["data_id"], target["group"], timeout=_CONNECT_TIMEOUT
            )
            config: dict[str, Any] = json.loads(raw) if raw else {}
            if not isinstance(config, dict):
                raise NacosUnavailable("Nacos 配置根值不是 JSON 对象，拒绝覆盖")
            for key, value in updates.items():
                if value is None:
                    config.pop(key, None)
                else:
                    config[key] = value
            published = client.publish_config(
                target["data_id"], target["group"],
                json.dumps(config, ensure_ascii=False, indent=2),
                config_type="json", timeout=_CONNECT_TIMEOUT,
            )
            if not published:
                raise NacosUnavailable("Nacos 拒绝写入，请检查权限")
        except NacosUnavailable:
            raise
        except Exception as exc:
            logger.warning("Nacos 配置写入失败 data_id=%s", target["data_id"], exc_info=True)
            raise NacosUnavailable("Nacos 写入失败，请检查连接或权限") from exc
        finally:
            reset()
        return config


def reset() -> None:
    """清除缓存，仅供测试使用。"""
    global _nacos_config, _nacos_loaded
    _nacos_config = None
    _nacos_loaded = False


__all__ = [
    "NacosUnavailable", "load_nacos_config", "nacos_bool", "nacos_get",
    "nacos_target", "publish_nacos_values", "reset",
]

