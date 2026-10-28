"""旧框架（spiders/）引用判定：prepare_delivery 与 finalize_execution 共用。

交付输入构造和终态报告输入构造需要用同一套口径决定报告目录按新框架还是
旧框架组织；此前两处各抄了一份 ``any(...)`` 判定，口径漂移会让同一任务在
steps 记录与 final.json 里落到不同的报告目录。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def legacy_reference(locate: Mapping[str, Any] | None) -> bool:
    """定位结论是否引用了旧框架 spiders/ 实现（源码证据或候选文件）。"""
    payload = locate if isinstance(locate, Mapping) else {}
    return any(
        str(item.get("path") or "").replace("\\", "/").startswith("spiders/")
        for item in payload.get("source_evidence") or []
        if isinstance(item, dict)
    ) or any(
        str(item).replace("\\", "/").startswith("spiders/")
        for item in payload.get("candidate_files") or []
    )


def report_framework(
    evidence: Mapping[str, Any] | None,
    locate: Mapping[str, Any] | None,
    framework: Any,
) -> str:
    """交付报告目录使用的框架名：命中旧框架证据时按 spiders 组织。"""
    payload = evidence if isinstance(evidence, Mapping) else {}
    return (
        "spiders"
        if payload.get("legacy_only")
        or payload.get("legacy_dependency")
        or legacy_reference(locate)
        else str(framework or "gathers")
    )


def report_dir(evidence: Mapping[str, Any] | None, framework: str, carrier: str) -> str:
    """报告目录：优先用证据里的真实 carrier 目录（保留源码大小写）。"""
    payload = evidence if isinstance(evidence, Mapping) else {}
    return (
        str(payload.get("carrier_dir") or "").replace("\\", "/").strip("/")
        or f"{framework}/{carrier}"
    )


def is_legacy_changed_path(path: Any) -> bool:
    """changed_files 中的路径是否指向旧框架 spiders/ 实现（大小写无关）。"""
    return str(path).replace("\\", "/").casefold().startswith("spiders/")


__all__ = ["is_legacy_changed_path", "legacy_reference", "report_dir", "report_framework"]
