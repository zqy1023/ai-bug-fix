"""模型服务目录与策略校验。

模型策略保存真实的 provider model id。前端只能从本模块返回的 ``available``
条目中选择，工作流启动和任务策略更新时会再次校验，避免前端或旧状态注入
未配置、拼写错误或当前账号不可用的模型。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse, urlunparse

import httpx

from app.infrastructure.config import ClaudeConfig, RuntimeConfig

ModelRole = Literal["analysis", "repair", "review"]
ROLES: tuple[ModelRole, ...] = ("analysis", "repair", "review")
_CACHE_TTL_SECONDS = 30.0
_catalog_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}


@dataclass(frozen=True)
class ModelOption:
    key: str
    model_id: str
    label: str
    roles: tuple[ModelRole, ...]
    available: bool
    reason: str | None = None
    is_default: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "model_id": self.model_id,
            "label": self.label,
            "roles": list(self.roles),
            "available": self.available,
            "reason": self.reason,
            "is_default": self.is_default,
        }


def configured_model_ids(claude: ClaudeConfig) -> tuple[str, ...]:
    """返回环境中声明的模型 ID，保持配置顺序并去重。"""
    values = [claude.model, *claude.models]
    seen: set[str] = set()
    return tuple(value for value in values if value and not (value in seen or seen.add(value)))


def _models_url(base_url: str) -> str:
    raw = (base_url or "").strip().rstrip("/")
    if not raw:
        return ""
    parsed = urlparse(raw)
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path + "/models"
    else:
        path = path + "/v1/models"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _probe_provider_models(claude: ClaudeConfig) -> tuple[list[str], str | None]:
    """从 provider 的 /v1/models 获取真实可用模型 ID。"""
    url = _models_url(claude.base_url)
    if not url:
        return [], "未配置 ANTHROPIC_BASE_URL"
    if not claude.auth_token:
        return [], "未配置 ANTHROPIC_AUTH_TOKEN"
    cache_key = (url, hashlib.sha256(claude.auth_token.encode()).hexdigest())
    cached = _catalog_cache.get(cache_key)
    now = time.monotonic()
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return [str(item["id"]) for item in cached[1]], None
    headers = {
        "x-api-key": claude.auth_token,
        "Authorization": f"Bearer {claude.auth_token}",
        "anthropic-version": "2023-06-01",
        "Accept": "application/json",
    }
    try:
        response = httpx.get(url, headers=headers, timeout=2.5)
        response.raise_for_status()
        payload = response.json()
    except Exception as error:
        return [], f"模型目录探测失败: {type(error).__name__}"
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return [], "模型目录响应格式无效"
    models = [
        {"id": str(item.get("id") or ""), "label": str(item.get("display_name") or item.get("id") or "")}
        for item in rows
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    _catalog_cache[cache_key] = (now, models)
    return [str(item["id"]) for item in models], None


def model_options(config: RuntimeConfig) -> list[ModelOption]:
    """构造供 API 和前端共用的模型目录。"""
    configured = configured_model_ids(config.claude)
    discovered, probe_error = _probe_provider_models(config.claude)
    discovered_set = set(discovered)
    ids = list(dict.fromkeys([*discovered, *configured]))
    default_id = config.claude.model
    return [
        ModelOption(
            key=model_id,
            model_id=model_id,
            label=model_id,
            roles=ROLES,
            available=model_id in discovered_set,
            reason=None if model_id in discovered_set else (probe_error or "模型服务未返回此模型"),
            is_default=model_id == default_id,
        )
        for model_id in ids
    ]


def model_options_by_role(config: RuntimeConfig) -> dict[str, list[dict[str, Any]]]:
    options = [item.as_dict() for item in model_options(config)]
    return {role: [item for item in options if role in item["roles"] and item["available"]] for role in ROLES}


def validate_policy_models(policy: Any, config: RuntimeConfig) -> None:
    """校验策略里的真实模型 ID；空字符串解析为运行时默认模型。"""
    available = {item.key for item in model_options(config) if item.available}
    for role in ROLES:
        field = {"analysis": "analysis_model", "repair": "repair_model", "review": "review_model"}[role]
        value = str(getattr(policy, field, "") or "").strip() or config.claude.model.strip()
        if not value:
            raise ValueError(f"{field} 未配置真实模型 ID")
        if value not in available:
            raise ValueError(f"{field} 不是当前模型服务可用的真实模型 ID: {value}")


__all__ = [
    "ModelOption",
    "ModelRole",
    "ROLES",
    "configured_model_ids",
    "model_options",
    "model_options_by_role",
    "validate_policy_models",
]
