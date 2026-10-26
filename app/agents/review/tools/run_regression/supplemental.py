"""将新采集正文作为独立补充样本，禁止冒充原始响应。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any


def verify_captured_response(
    request: dict[str, Any], record: dict[str, Any], payload: dict[str, Any], callback: Callable[..., Any],
) -> dict[str, Any]:
    """仅原正文缺失且新请求成功时补双侧验证；原异常未复现仍为未验证。"""
    raw = record.get("req") or {}
    raw = json.loads(raw) if isinstance(raw, str) else raw
    if record.get("response") or (isinstance(raw, dict) and raw.get("response")):
        return {}
    body = request.get("response")
    if request.get("status") != "verified" or not isinstance(body, str) or not body.strip():
        return {}
    sample = {"kind": "newly_captured_response", "entrypoint": "server_dev.main",
              "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(), "original_recording": False}
    result = callback({**payload, "normalized_log": {**record, "response": body}})
    if not isinstance(result, dict):
        return {"passed": False, "status": "unverified", "reason": "新正文补充验证未返回有效结果", "sample_source": sample}
    return {**result, "sample_source": sample}
