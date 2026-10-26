"""在业务子进程观测框架错误，保留原日志调用，不修改业务文件。"""

from __future__ import annotations

import inspect
import threading
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


@contextmanager
def observe_business_errors() -> Iterator[dict[str, Any]]:
    """仅包围本次入口执行；缺少业务日志模块时明确记录不可观测。"""
    observation: dict[str, Any] = {"available": False, "errors": []}
    try:
        from common.log_utils import LogUtils
    except ImportError:
        yield observation
        return
    original = LogUtils.error
    descriptor = inspect.getattr_static(LogUtils, "error")
    owner_thread = threading.get_ident()

    def observed(*args: Any, **kwargs: Any) -> Any:
        category = args[2] if len(args) > 2 else kwargs.get("biztype", "")
        if threading.get_ident() == owner_thread:
            frames = traceback.extract_stack()[:-1]
            business_related = category in {"时间格式错误", "状态格式错误", "地址格式错误"} or any(
                frame.name == "process_html" or "/framework/result_format/" in frame.filename.replace("\\", "/")
                for frame in frames
            )
            observation["errors"].append({
                "category": str(category or ""), "business_related": business_related,
                "message": str(args[0] if args else kwargs.get("message", "")),
                "frames": [{"file": frame.filename, "line": frame.lineno, "function": frame.name} for frame in frames],
            })
        return original(*args, **kwargs)

    LogUtils.error = staticmethod(observed)
    observation["available"] = True
    try:
        yield observation
    finally:
        LogUtils.error = descriptor
