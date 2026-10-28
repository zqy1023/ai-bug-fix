"""工作台运行时上下文、基础存取与访问控制依赖。"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Header, HTTPException

from infrastructure.llm.config import load_config
from infrastructure.storage.store import RecordStore
from infrastructure.storage.redaction import public_exception

logger = logging.getLogger("workbench.context")

SERVICE_NAME = "ai-repair-workbench"
SERVICE_VERSION = "1.0.0"

from config.nacos import nacos_get
from config.settings import settings

_ID_MAX_LENGTH = 160
_ID_PATTERN = re.compile(rf"^[A-Za-z0-9_.-]{{1,{_ID_MAX_LENGTH}}}$")
_RUN_LOCKS: OrderedDict[str, threading.Lock] = OrderedDict()
_RUN_LOCKS_GUARD = threading.Lock()
_RUN_LOCKS_LIMIT = 4096
_STARTED_RUNS: set[str] = set()

def _authorize(authorization: str | None = Header(default=None)) -> None:
    token = nacos_get("WORKBENCH_API_TOKEN", "").strip()
    if not token:
        return
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not supplied or not secrets.compare_digest(supplied, token):
        raise HTTPException(status_code=401, detail="需要有效的工作台 Bearer Token")

def _config():
    try:
        return load_config()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=public_exception(exc, prefix="运行配置不可用"),
        ) from exc

def _store() -> RecordStore:
    config = _config()
    return RecordStore(config.runtime_dir)

def _safe_id(value: str) -> str:
    if not _ID_PATTERN.fullmatch(value) or value in {".", ".."}:
        raise HTTPException(status_code=400, detail="无效的 ID")
    return value

def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default

def _run_lock(run_id: str) -> threading.Lock:
    """获取（或创建）run 的执行锁；必须在 _RUN_LOCKS_GUARD 内调用。

    命中即 move_to_end 触碰 LRU 顺序；超过 _RUN_LOCKS_LIMIT 时淘汰最旧条目。
    使用中的锁对象仍被运行线程持有，淘汰只让后续请求重建锁，属可接受的取舍。
    """
    lock = _RUN_LOCKS.get(run_id)
    if lock is None:
        lock = threading.Lock()
        _RUN_LOCKS[run_id] = lock
    else:
        _RUN_LOCKS.move_to_end(run_id)
    while len(_RUN_LOCKS) > _RUN_LOCKS_LIMIT:
        _RUN_LOCKS.popitem(last=False)
    return lock

def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None

def _require_current_workflow(run_id: str, store: RecordStore | None = None) -> None:
    from workflow.engine.validation import assert_workflow_version
    s = store or _store()
    try:
        assert_workflow_version(s.load_run(run_id))
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
