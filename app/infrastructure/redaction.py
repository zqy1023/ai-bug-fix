"""脱敏：把凭据掩码掉，键名尽量保留，便于定位是哪一类凭据泄露。

``mask_secrets`` 是从交付提交说明里提取出来的既有规则，行为逐字不变；运行记录在
它之上叠加 ``redact_output`` 的报告专属规则。两份实现不各写一套，也不再新增第三份。
"""

from __future__ import annotations

import re

from git.exc import GitCommandError

from app.domain.errors import DeliveryError, StepFailed

# 命中敏感键名后把取值掩码成 ***，键名保留：说得出"哪个字段泄露了"才有排查价值。
_SECRET_WORDS = re.compile(
    r"(?i)\b(token|secret|password|passwd|authorization|cookie|bearer)\b\s*[:=]?\s*\S+"
)

# 只属于运行记录的额外规则。共用规则一次只吃一个 ``\S+``：JSON 里键名带引号时它
# 只吃掉引号，跟在 Bearer 后面的令牌本体留在明文里。所以先按行内结构整段掩码，
# 再套共用规则兜底。
_UNSAFE_DETAIL = re.compile(r"\binput_(?:value|type)\s*=\s*[^\r\n]*", re.IGNORECASE)
_MAX_PUBLIC_MESSAGE_CHARS = 500

_REPORT_ONLY = (
    re.compile(
        r"(?i)\"?(?:authorization|proxy-authorization|cookie|set-cookie)\"?\s*[:=]\s*[^\r\n,;}]+"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]{8,}"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|apikey|access[_-]?key|secret[_-]?key|private[_-]?key|client[_-]?secret)"
        r"\b\"?\s*[:=]\s*[^\s,;}\"']+"
    ),
)


def mask_secrets(text: str) -> str:
    """掩码敏感键名后的取值，保留键名。交付提交说明与运行记录共用。"""
    return _SECRET_WORDS.sub(lambda match: match.group(0).split(":")[0].split("=")[0] + ": ***", text)


def redact_output(text: str) -> str:
    """运行记录用的脱敏：报告专属规则 + 共用规则。"""
    masked = text
    masked = re.sub(
        r"(?i)(\b(?:trackingIds?|tracking[_-]?(?:number|no)|waybill(?:[_-]?(?:number|no))?|number)\b[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,;}<)]+",
        r"\1***", masked,
    )
    masked = re.sub(r"((?:运单号|单号|包裹号)\s*[:：=]?\s*)[A-Za-z0-9]{8,}", r"\1***", masked)
    for pattern in _REPORT_ONLY:
        masked = pattern.sub("***", masked)
    return mask_secrets(masked)


def public_exception(error: BaseException, *, prefix: str) -> str:
    """返回可持久化的异常摘要。

    业务异常（``StepFailed`` / ``DeliveryError``）的消息本身就是给人看的失败原因，
    脱敏后保留并限长；其它异常只保留异常类型，避免把密钥、单号或模型原文写进
    步骤记录。pydantic ``ValidationError`` 只保留字段名和错误类型，不保留输入值。
    """
    error_type = type(error).__name__ or "Exception"
    if isinstance(error, GitCommandError):
        # Git commands may embed credentials in URLs; expose only scrubbed stderr.
        detail = str(error.stderr or "").strip()
        detail = re.sub(r"https?://[^\s/'\"]+", "https://***", detail)
        detail = re.sub(r"(?i)(?:token|password|secret|api[_-]?key)\s*[:=]\s*[^\s]+", "***", detail)
        detail = redact_output(" ".join(detail.split()))[:_MAX_PUBLIC_MESSAGE_CHARS]
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
    """脱敏并限长业务异常消息；非业务异常不暴露正文。"""
    # Import lazily: the source adapter itself imports the redaction helpers.
    from app.infrastructure.git.source import SourceRepoError

    if not isinstance(error, (StepFailed, DeliveryError, SourceRepoError)):
        return ""
    text = _UNSAFE_DETAIL.sub("", str(error))
    text = re.sub(r"(https?://)[^\s/'\"]+@", r"\1***@", text)
    text = re.sub(r"(?i)((?:access[_-]?token|token|password|secret|api[_-]?key)=)[^&\s\"']+", r"\1***", text)
    text = redact_output(" ".join(text.split())).strip()
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
