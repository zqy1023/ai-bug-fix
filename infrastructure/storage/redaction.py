"""运行产物与正式交付的错误呈现和敏感信息处理。"""

from __future__ import annotations

import re
from typing import Any

from git.exc import GitCommandError

from core.errors import DeliveryError, StepFailed

_MAX_PUBLIC_MESSAGE_CHARS = 500


def redact_value(value: Any) -> Any:
    """保留结构和值，避免重写 JSON 破坏转义。"""
    from copy import deepcopy
    return deepcopy(value)


# ---------------------------------------------------------------------------
# 交付脱敏（两级管线的外圈）。
#
# 内部审计产物（records、artifacts、完整回归报告）仍走 原样
# 落盘，便于排查；只有会出现在 Git 上的交付面（commit message、task_summary.md）
# 走 ``redact_for_delivery``。这里刻意保守：只覆盖凭据几乎必然出现的几种形态，
# 不做内网地址、单号之类的替换，避免误伤正文导致交付内容不可读。
# ---------------------------------------------------------------------------

_DELIVERY_MASK = "***"

# URL userinfo：https://user:pass@host → https://***@host。
_DELIVERY_URL_USERINFO = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+@")

# 鉴权头：Authorization / Proxy-Authorization 的值整段掩掉（含 Bearer/Basic/Token 前缀）。
_DELIVERY_AUTH_HEADER = re.compile(
    r"(?i)(?P<pre>\b(?P<header>authorization|proxy-authorization)[\"']?\s*:\s*)"
    r"(?P<q>[\"']?)(?:(?P<scheme>bearer|basic|token|digest)\s*)?(?P<value>[^\s\"',}]+)"
)

# Cookie 头的值整段掩掉（同样命中 set-cookie 的值部分）。
_DELIVERY_COOKIE_HEADER = re.compile(
    r"(?i)(?P<pre>\b(?P<header>cookie)[\"']?\s*:\s*)(?P<q>[\"']?)(?P<value>[^\r\n\"']+)"
)

# 查询参数：access_token=... / token=... 等令牌参数的值。
_DELIVERY_QUERY_TOKEN = re.compile(
    r"(?i)(?P<lead>[?&#])(?P<name>access_token|auth_token|id_token|refresh_token|api_key|apikey|token)"
    r"[\"']?(?P<sep>[:=])(?P<sp>\s*)(?P<q>[\"']?)(?P<value>[^&\s\"',}]+)"
)

# AWS 风格长密钥：仅在明确的 key 名前缀后掩 20+ 位 base64/hex 连续串。
_DELIVERY_LONG_KEY = re.compile(
    r"(?i)\b(?P<name>(?:aws[_-]?)?(?:access[_-]?key[_-]?id|secret[_-]?access[_-]?key"
    r"|access[_-]?key|secret[_-]?key|api[_-]?key|apikey))\s*[=:]\s*[\"']?"
    r"(?P<value>[A-Za-z0-9/+]{20,})"
)

# 强凭据赋值：password=... / client_secret: ... 等。键名几乎不会出现在交付正文里，
# 因此只要有值就整段掩掉（同时覆盖 JSON 形态 "password": "..."）。
_DELIVERY_CREDENTIAL_ASSIGN = re.compile(
    r"(?i)(?<![A-Za-z0-9_.-])(?P<name>password|passwd|pwd|passphrase|client[_-]?secret"
    r"|app[_-]?secret|consumer[_-]?secret|private[_-]?key|secret[_-]?key|api[_-]?key"
    r"|api[_-]?secret)(?P<kq>[\"']?)(?P<sep>[=:])(?P<sp>\s*)(?P<q>[\"']?)(?P<value>[^\s\"'`,;&)\]}]+)"
)

# 弱凭据赋值：secret / token 也可能是正文用语，只掩足够长、且像凭据的值，
# 避免把 "secret: 已轮换" 这类说明文字抹掉。
_DELIVERY_WEAK_SECRET_ASSIGN = re.compile(
    r"(?i)(?<![A-Za-z0-9_.-])(?P<name>secret|token)(?P<kq>[\"']?)(?P<sep>[=:])(?P<sp>\s*)"
    r"(?P<q>[\"']?)(?P<value>[A-Za-z0-9!#$%&'*+\-./:;<=>?@^_`|~]{6,})"
)

# 裸 AWS Access Key ID（AKIA/ASIA…）：长度与字符集固定，误报概率极低。
_DELIVERY_AWS_KEY_ID = re.compile(
    r"\b(?P<id>(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[0-9A-Z]{16})\b"
)

# PEM 私钥块：整块掩掉（公钥不处理）。
_DELIVERY_PEM = re.compile(
    r"-----BEGIN[A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"
    r".*?"
    r"-----END[A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----",
    re.DOTALL,
)


def redact_for_delivery(text: str) -> str:
    """掩掉交付面上几乎必然是凭据的片段；其余文本原样保留。

    内部审计保留原文，本函数只用于离开本系统的
    交付面。覆盖：URL userinfo、Bearer/Token 鉴权头、Cookie 头、令牌型查询参数、
    明确 key 名前缀后的 AWS 风格长密钥、password/secret 类赋值、PEM 私钥块、
    裸 AWS Access Key ID。掩码统一为 ``***``。
    """
    raw = str(text or "")
    if not raw:
        return raw
    raw = _DELIVERY_URL_USERINFO.sub(r"\g<scheme>***@", raw)
    raw = _DELIVERY_AUTH_HEADER.sub(
        lambda m: f"{m.group('pre')}{m.group('q')}{_DELIVERY_MASK}", raw
    )
    raw = _DELIVERY_COOKIE_HEADER.sub(
        lambda m: f"{m.group('pre')}{m.group('q')}{_DELIVERY_MASK}", raw
    )
    raw = _DELIVERY_QUERY_TOKEN.sub(
        lambda m: (
            f"{m.group('lead')}{m.group('name')}{m.group('sep')}"
            f"{m.group('sp')}{m.group('q')}{_DELIVERY_MASK}"
        ),
        raw,
    )
    raw = _DELIVERY_LONG_KEY.sub(lambda m: f"{m.group('name')}={_DELIVERY_MASK}", raw)
    raw = _DELIVERY_CREDENTIAL_ASSIGN.sub(
        lambda m: (
            f"{m.group('name')}{m.group('kq')}{m.group('sep')}{m.group('sp')}"
            f"{m.group('q')}{_DELIVERY_MASK}"
        ),
        raw,
    )
    raw = _DELIVERY_WEAK_SECRET_ASSIGN.sub(
        lambda m: (
            f"{m.group('name')}{m.group('kq')}{m.group('sep')}{m.group('sp')}"
            f"{m.group('q')}{_DELIVERY_MASK}"
        ),
        raw,
    )
    raw = _DELIVERY_PEM.sub(_DELIVERY_MASK, raw)
    raw = _DELIVERY_AWS_KEY_ID.sub(_DELIVERY_MASK, raw)
    return raw


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
    from infrastructure.git.source import SourceRepoError

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


__all__ = ["public_exception", "redact_for_delivery", "redact_value"]
