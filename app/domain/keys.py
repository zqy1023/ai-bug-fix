"""任务键、问题指纹和安全目录名。"""

from __future__ import annotations

import hashlib
import re
from typing import Any

_VOLATILE = re.compile(
    r"\b(?:request[_-]?id|trace[_-]?id|timestamp|time)\s*[=:]\s*[^,; ]+",
    re.I,
)
# 问题指纹不能随时间漂移：真实时间值保留在步骤记录里，但计算指纹前归一化。
_TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
# 分支目录要能按真实 biz_type 分类：保留 Unicode 字母（如中文），只替换
# 空白、斜杠和 Git ref 非法字符；任务目录名仍使用 ASCII 的 safe_name。
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
    """按业务类型、carrier 与问题指纹生成稳定分支名，避免不同异常互相覆盖。

    biz_type 直接作为一级目录，同一业务类型的所有修复分支收敛在同一目录；
    中文 biz_type 不再被压成 ``item``。
    """
    carrier_name = branch_segment(carrier)
    if fingerprint:
        carrier_name = f"{carrier_name}-{safe_name(fingerprint)[:8]}"
    return f"{prefix}/{branch_segment(biztype)}/{carrier_name}"
