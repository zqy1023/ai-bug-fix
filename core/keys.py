"""任务指纹、问题 Key、分支名称算法与安全 JSON 处理。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any



import json
import re

_FENCE_PATTERN = r"```(?:json|JSON)?\s*(\{.*?\})\s*```"


def loads_object(text: Any) -> dict[str, Any] | None:
    """返回文本中的第一个有效 JSON 字典对象；无法解析时返回 None。"""
    if not isinstance(text, str) or not text.strip():
        return None

    candidates: list[str] = [
        match.group(1) for match in re.finditer(_FENCE_PATTERN, text, re.DOTALL)
    ]
    candidates.append(text)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(payload, dict):
            return payload
    return None





import hashlib
import re
from datetime import datetime, timezone

_VOLATILE = re.compile(
    r"\b(?:request[_-]?id|trace[_-]?id|timestamp|time)\s*[=:]\s*[^,; ]+",
    re.I,
)
_TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_BRANCH_UNSAFE = re.compile(r"[^\w.-]+", re.UNICODE)


def branch_segment(value: str) -> str:
    """生成可读的分支路径片段，保留中文 biz_type。"""
    return _BRANCH_UNSAFE.sub("-", str(value)).strip("-.") or "item"


def safe_name(value: str) -> str:
    """把任意标识转换为可用于文件和目录的安全片段。"""
    return _UNSAFE.sub("-", str(value)).strip("-.") or "item"


def problem_key(problem_input: dict[str, Any]) -> str:
    """由稳定错误特征生成问题指纹，过滤易变日志字段。"""
    error = _TIMESTAMP.sub("<timestamp>", str(problem_input.get("error", "")))
    error = _VOLATILE.sub("", error).strip()
    values = (
        problem_input.get("service_name", ""),
        problem_input.get("biztype", ""),
        problem_input.get("carrier", ""),
        error,
    )
    return hashlib.sha256("|".join(map(str, values)).encode("utf-8")).hexdigest()[:24]


def task_key(run_id: str, biztype: str, carrier: str, fingerprint: str) -> str:
    """生成任务幂等键。"""
    return f"{run_id}/{safe_name(biztype)}/{safe_name(carrier)}/{fingerprint}"


def task_id(biztype: str, carrier: str, fingerprint: str) -> str:
    """生成在磁盘上稳定且可读的任务目录名。"""
    short = fingerprint[:12]
    return f"{safe_name(carrier)}__{safe_name(biztype)}__{short}"


def repair_branch(
    biztype: str,
    carrier: str,
    prefix: str = "ai-bug-tasks",
    fingerprint: str = "",
) -> str:
    """按业务类型、carrier 与问题指纹生成稳定分支名，避免不同异常互相覆盖。"""
    carrier_name = branch_segment(carrier)
    if fingerprint:
        carrier_name = f"{carrier_name}-{safe_name(fingerprint)[:8]}"
    return f"{prefix}/{branch_segment(biztype)}/{carrier_name}"


def utc_now() -> str:
    """返回带时区的 ISO 时间戳。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


