"""脱敏：把凭据掩码掉，键名尽量保留，便于定位是哪一类凭据泄露。

``mask_secrets`` 是从交付提交说明里提取出来的既有规则，行为逐字不变；运行记录在
它之上叠加 ``redact_output`` 的报告专属规则。两份实现不各写一套，也不再新增第三份。
"""

from __future__ import annotations

import re

# 命中敏感键名后把取值掩码成 ***，键名保留：说得出"哪个字段泄露了"才有排查价值。
_SECRET_WORDS = re.compile(
    r"(?i)\b(token|secret|password|passwd|authorization|cookie|bearer)\b\s*[:=]?\s*\S+"
)

# 只属于运行记录的额外规则。共用规则一次只吃一个 ``\S+``：JSON 里键名带引号时它
# 只吃掉引号，跟在 Bearer 后面的令牌本体留在明文里。所以先按行内结构整段掩码，
# 再套共用规则兜底。
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
    for pattern in _REPORT_ONLY:
        masked = pattern.sub("***", masked)
    return mask_secrets(masked)


def public_exception(error: BaseException, *, prefix: str) -> str:
    """返回可持久化的异常摘要。

    默认只保留异常类型，避免把密钥、单号或模型原文写进步骤记录。
    pydantic ``ValidationError`` 只保留字段名和错误类型，不保留输入值。
    """
    error_type = type(error).__name__ or "Exception"
    detail = _validation_detail(error)
    if detail:
        return f"{prefix}（错误类型：{error_type}：{detail}）"
    return f"{prefix}（错误类型：{error_type}）"


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
