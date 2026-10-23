"""运行输出原样保留；旧函数名仅用于兼容既有调用点。"""

from __future__ import annotations

import re
from typing import Any

from git.exc import GitCommandError

from app.domain.errors import DeliveryError, StepFailed

_MAX_PUBLIC_MESSAGE_CHARS = 500


def mask_secrets(text: str) -> str:
    """按项目设置原样保留日志及交付说明。"""
    return text


def redact_output(text: str) -> str:
    """兼容旧调用点；新运行原样保存输出，不替换单号或凭据。"""
    return text


def redact_value(value: Any) -> Any:
    """保留结构和值，避免重写 JSON 破坏转义。"""
    from copy import deepcopy
    return deepcopy(value)


def public_exception(error: BaseException, *, prefix: str) -> str:
    """返回可持久化的异常摘要。

    业务异常（``StepFailed`` / ``DeliveryError``）的消息本身就是给人看的失败原因，
    业务异常的摘要限长，完整输出另存归档。pydantic ``ValidationError`` 摘要列出
    字段位置，原始异常与模型输出由调用审计保留。
    """
    error_type = type(error).__name__ or "Exception"
    if isinstance(error, GitCommandError):
        detail = str(error.stderr or error).strip()
        return f"{prefix}（错误类型：{error_type}，exit={error.status}）：{detail}"
    detail = _validation_detail(error)
    if detail:
        return f"{prefix}（错误类型：{error_type}：{detail}）"
    # AttributeError 是交付链路常见的集成缺陷；安全地暴露缺失属性名，
    # 避免运行记录只留下“AttributeError”而无法定位调用方/接口契约不一致。
    if isinstance(error, AttributeError):
        match = re.search(
            r"'([^']+)' object has no attribute '([^']+)'",
            str(error),
        )
        if match:
            return (
                f"{prefix}（错误类型：AttributeError："
                f"{match.group(1)} 缺少属性 {match.group(2)}）"
            )
    message = _business_message(error)
    if message:
        cause = error.__cause__
        if isinstance(cause, GitCommandError):
            message += "；" + public_exception(cause, prefix="Git")
        return f"{prefix}：{message}（错误类型：{error_type}）"
    return f"{prefix}（错误类型：{error_type}）"


def _business_message(error: BaseException) -> str:
    """业务异常生成限长摘要；原始日志内容不改写。"""
    # Import lazily: the source adapter itself imports the redaction helpers.
    from app.infrastructure.git.source import SourceRepoError

    if not isinstance(error, (StepFailed, DeliveryError, SourceRepoError)):
        return ""
    text = " ".join(str(error).split())
    if len(text) > _MAX_PUBLIC_MESSAGE_CHARS:
        # Git clone progress/path prefixes can be long; the actual fatal reason
        # is usually at the end of stderr and must survive truncation.
        return text[:180] + " … " + text[-(_MAX_PUBLIC_MESSAGE_CHARS - 185):]
    return text


def _validation_detail(error: BaseException) -> str:
    """抽出校验失败的字段位置，不带入被拒绝的原始值。"""
    errors = getattr(error, "errors", None)
    if not callable(errors):
        return ""
    try:
        items = errors()
    except Exception:
        return ""
    if not isinstance(items, list):
        return ""
    parts: list[str] = []
    for item in items[:8]:
        if not isinstance(item, dict):
            continue
        loc = ".".join(str(part) for part in item.get("loc") or ()) or "<root>"
        kind = str(item.get("type") or "invalid")
        parts.append(f"{loc}[{kind}]")
    hidden = len(items) - len(parts)
    if hidden > 0:
        parts.append(f"另有 {hidden} 项")
    return "；".join(parts)


__all__ = ["mask_secrets", "public_exception", "redact_output"]
