"""任务键、问题指纹和安全目录名。"""

from __future__ import annotations

import hashlib
import re
from typing import Any

_VOLATILE = re.compile(
    r"\b(?:request[_-]?id|trace[_-]?id|timestamp|time)\s*[=:]\s*[^,; ]+",
    re.I,
)
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(value: str) -> str:
    """把任意标识转换为可用于文件和目录的安全片段。"""
    return _UNSAFE.sub("-", str(value)).strip("-.") or "item"


def problem_key(problem_input: dict[str, Any]) -> str:
    """由稳定错误特征生成问题指纹，过滤易变日志字段。"""
    error = _VOLATILE.sub("", str(problem_input.get("error", ""))).strip()
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
    prefix: str = "ai-repair",
    fingerprint: str = "",
) -> str:
    """按业务类型、carrier 与问题指纹生成稳定分支名，避免不同异常互相覆盖。"""
    carrier_name = safe_name(carrier)
    if fingerprint:
        carrier_name = f"{carrier_name}-{safe_name(fingerprint)[:8]}"
    return f"{prefix}/{safe_name(biztype)}/{carrier_name}"
