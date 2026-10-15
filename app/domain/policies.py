"""可处理服务注册表。服务资格与 Agent 提示词目录解耦。"""

from __future__ import annotations

import json
from pathlib import Path


class ServiceRegistryError(ValueError):
    """服务注册配置无法读取或校验。"""


def registered_services() -> frozenset[str]:
    """读取并校验 ``config/services/*.json`` 服务登记。"""
    directory = Path(__file__).resolve().parents[2] / "config" / "services"
    manifests = sorted(directory.glob("*.json"))
    if not manifests:
        raise ServiceRegistryError(f"服务注册目录没有 JSON 配置: {directory}")
    services: list[str] = []
    for manifest in manifests:
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ServiceRegistryError(f"服务配置读取失败: {manifest}") from error
        service_name = payload.get("service_name") if isinstance(payload, dict) else None
        if not isinstance(service_name, str) or not service_name.strip():
            raise ServiceRegistryError(f"服务配置必须包含非空 service_name: {manifest}")
        services.append(service_name)
    if len(services) != len(set(services)):
        raise ServiceRegistryError(f"服务配置存在重复 service_name: {directory}")
    return frozenset(services)


def is_registered_service(service_name: str) -> bool:
    """判断 ES 原始服务名是否被当前工作流注册。"""
    if not service_name:
        return False
    return service_name in registered_services()


__all__ = ["ServiceRegistryError", "is_registered_service", "registered_services"]
