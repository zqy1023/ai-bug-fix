"""本进程复用的模型会话池。

步骤记录仓库不再放在这里：它由 :class:`app.application.context.StepContext` 显式注入，
插件也不允许再调用 ``current_store()``。本模块只负责同一工作区、同一角色复用一个
长连接会话（常驻 Claude 会话），并提供调试与整图收尾时的统一关闭入口。
"""

from __future__ import annotations

import os
import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from config.settings import settings

@dataclass
class _SessionEntry:
    session: Any
    run_id: str
    task_id: str
    fingerprint: str = ""
    users: int = 0
    closing: bool = False


_SESSIONS: dict[tuple[str, str], _SessionEntry] = {}
_SESSION_LOCK = threading.RLock()
# 常驻会话上限：每个 task+carrier 各一个会话，长跑/服务化时不释放会持续堆积。
_MAX_SESSIONS = max(1, int(os.getenv("AI_REPAIR_MAX_SESSIONS", str(settings.max_sessions))))


def _close(session: Any) -> None:
    close = getattr(session, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            logging.getLogger(__name__).exception("关闭任务模型会话失败")


def close_sessions(run_id: str | None = None, task_id: str | None = None) -> None:
    """释放指定执行的会话；在用会话等最后一个租用结束后再关闭。"""
    sessions: list[Any] = []
    with _SESSION_LOCK:
        for key, entry in list(_SESSIONS.items()):
            if run_id is not None and entry.run_id != run_id:
                continue
            if task_id is not None and entry.task_id != task_id:
                continue
            entry.closing = True
            if not entry.users:
                sessions.append(_SESSIONS.pop(key).session)
    for session in sessions:
        _close(session)


@contextmanager
def session_scope(
    kind: str, workspace: str, factory: Callable[[], Any], *, run_id: str, task_id: str,
    fingerprint: str = "",
) -> Iterator[Any]:
    """租用本任务会话；活跃调用期间禁止容量淘汰或提前关闭。"""
    key = (kind, workspace)
    retired: list[Any] = []
    with _SESSION_LOCK:
        entry = _SESSIONS.get(key)
        if entry is not None and entry.fingerprint != fingerprint:
            if entry.users:
                raise RuntimeError("会话规则已变化，等待当前调用结束后重试")
            retired.append(_SESSIONS.pop(key).session)
            entry = None
        if entry is not None and entry.closing:
            raise RuntimeError("该任务会话正在关闭，不能再次提交请求")
        if entry is None:
            created = _SessionEntry(factory(), run_id, task_id, fingerprint=fingerprint)
            for oldest, candidate in list(_SESSIONS.items()):
                if len(_SESSIONS) < _MAX_SESSIONS:
                    break
                if not candidate.users:
                    retired.append(_SESSIONS.pop(oldest).session)
            entry = created
        _SESSIONS.pop(key, None)
        _SESSIONS[key] = entry
        entry.users += 1
    try:
        for session in retired:
            _close(session)
        yield entry.session
    finally:
        closed: list[Any] = []
        with _SESSION_LOCK:
            entry.users -= 1
            if entry.closing and not entry.users and _SESSIONS.get(key) is entry:
                closed.append(_SESSIONS.pop(key).session)
            for oldest, candidate in list(_SESSIONS.items()):
                if len(_SESSIONS) <= _MAX_SESSIONS:
                    break
                if not candidate.users:
                    closed.append(_SESSIONS.pop(oldest).session)
        for session in closed:
            _close(session)


def drop_session(kind: str, workspace: str) -> None:
    """移除并关闭失效会话，避免超时后继续向死亡线程投递请求。"""
    with _SESSION_LOCK:
        entry = _SESSIONS.get((kind, workspace))
        if entry is None:
            return
        entry.closing = True
        session = _SESSIONS.pop((kind, workspace)).session if not entry.users else None
    if session is not None:
        _close(session)


__all__ = ["close_sessions", "drop_session", "session_scope"]
