"""工作台运行控制信号的持久化读取。"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from pathlib import Path

logger = logging.getLogger(__name__)


class ControlStoreUnavailable(RuntimeError):
    """控制存储不可用；调用方必须停止副作用执行，不能按“未请求”处理。"""


def _control_flags(runtime_dir: str | Path, run_id: str) -> tuple[bool, bool]:
    """返回 (pause_requested, cancel_requested)。

    控制库尚未创建时表示当前没有持久化控制请求；但库已存在却无法读取时，
    必须 fail closed，防止暂停/取消信号因数据库故障而被静默忽略。
    """
    database = Path(runtime_dir) / "workbench-control.sqlite3"
    if not run_id or not database.is_file():
        return False, False
    try:
        with closing(sqlite3.connect(str(database), timeout=1)) as connection:
            row = connection.execute(
                "SELECT pause_requested, cancel_requested FROM run_control WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            return (bool(row[0]), bool(row[1])) if row else (False, False)
    except sqlite3.Error as error:
        logger.exception("读取运行控制信号失败（run_id=%s）", run_id)
        raise ControlStoreUnavailable(
            f"运行控制存储不可用，已阻止后续执行（run_id={run_id}）"
        ) from error


def pause_requested(runtime_dir: str | Path, run_id: str) -> bool:
    """读取持久化暂停意图；控制存储故障时抛出异常而不是静默放行。"""
    return _control_flags(runtime_dir, run_id)[0]


def cancellation_requested(runtime_dir: str | Path, run_id: str) -> bool:
    """读取持久化取消意图；控制存储故障时抛出异常而不是静默放行。"""
    return _control_flags(runtime_dir, run_id)[1]


__all__ = ["ControlStoreUnavailable", "cancellation_requested", "pause_requested"]
