"""原始日志到采集请求的无依赖提取。"""
from __future__ import annotations
import json
import re
from typing import Any


def original_request(problem: dict[str, Any]) -> dict[str, Any]:
    """原始 model 优先，完整保留请求项与配置；response 不能用于替代真实请求。"""
    text = str(problem.get("original_exception_message") or problem.get("message") or "")
    match = re.search(r"model\s*:\s*", text)
    if match:
        try:
            value, _ = json.JSONDecoder().raw_decode(text[match.end():])
            if isinstance(value, dict) and value.get("requests"):
                return {key: value[key] for key in ("carrier", "requests", "track_config") if key in value}
        except ValueError:
            pass
    value = problem.get("req") or {}
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("缺少原始请求")
    request = {key: value[key] for key in ("carrier", "requests", "track_config") if key in value}
    request.setdefault("carrier", problem.get("carrier"))
    if "track_config" not in request and problem.get("track_config"):
        config = problem["track_config"]
        request["track_config"] = json.loads(config) if isinstance(config, str) else config
    return request

