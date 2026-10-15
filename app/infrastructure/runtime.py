"""本进程复用的模型会话池。

步骤记录仓库不再放在这里：它由 :class:`app.application.context.StepContext` 显式注入，
插件也不允许再调用 ``current_store()``。本模块只负责同一工作区、同一角色复用一个
长连接会话（常驻 Claude 会话），并提供调试与整图收尾时的统一关闭入口。
"""

from __future__ import annotations

import os
from typing import Any

from config.settings import settings

_SESSIONS: dict[tuple[str, str], Any] = {}
# 常驻会话上限：每个 task+carrier 各一个会话，长跑/服务化时不释放会持续堆积。
_MAX_SESSIONS = max(1, int(os.getenv("AI_REPAIR_MAX_SESSIONS", str(settings.max_sessions))))


def _close(session: Any) -> None:
    close = getattr(session, "close", None)
    if callable(close):
        close()


def close_sessions() -> None:
    """关闭本进程复用的模型会话。整图跑完、单步调试结束都应调用，避免常驻 SDK
    会话与后台线程泄漏。"""
    sessions = list(_SESSIONS.values())
    _SESSIONS.clear()
    for session in sessions:
        _close(session)


def shared_session(kind: str, workspace: str, factory: Any) -> Any:
    """同一会话键只建一个会话；超出上限时淘汰最久未用的那个。

    单步调试和整图共用同一份缓存。命中时刷新最近使用顺序，淘汰总是发生在最少
    使用的会话上，避免把正在使用的会话挤掉。
    """
    key = (kind, workspace)
    session = _SESSIONS.get(key)
    if session is not None:
        _SESSIONS.pop(key)
        _SESSIONS[key] = session  # 移到末尾 = 最近使用
        return session
    while len(_SESSIONS) >= _MAX_SESSIONS:
        oldest_key = next(iter(_SESSIONS))
        _close(_SESSIONS.pop(oldest_key))
    session = factory()
    _SESSIONS[key] = session
    return session


def drop_session(kind: str, workspace: str) -> None:
    """移除并关闭失效会话，避免超时后继续向死亡线程投递请求。"""
    session = _SESSIONS.pop((kind, workspace), None)
    if session is not None:
        _close(session)


__all__ = ["close_sessions", "drop_session", "shared_session"]
