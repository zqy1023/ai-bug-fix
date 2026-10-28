"""工作台运行控制信号的持久化读取。"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)


def _control_flags(runtime_dir: str | Path, run_id: str) -> tuple[bool, bool]:
    """返回 (pause_requested, cancel_requested)；控制库不可用时不伪造控制请求。"""
    database = Path(runtime_dir) / "workbench-control.sqlite3"
    if not run_id or not database.is_file():
        return False, False
    try:
        with closing(sqlite3.connect(
            str(database),
            timeout=1,
        )) as connection:
            row = connection.execute(
                "SELECT pause_requested, cancel_requested FROM run_control WHERE run_id = ?", (run_id,)
            ).fetchone()
            return (bool(row[0]), bool(row[1])) if row else (False, False)
    except sqlite3.Error as error:
        # 控制库读不到时不能伪造"未请求"，但也绝不能静默：用户的取消/暂停
        # 会就此失效，工作流继续跑完整轮，日志里还得留得下痕迹。
        logger.warning("读取运行控制信号失败（run_id=%s）：%s", run_id, error)
        return False, False


def pause_requested(runtime_dir: str | Path, run_id: str) -> bool:
    """读取持久化暂停意图。"""
    return _control_flags(runtime_dir, run_id)[0]


def cancellation_requested(runtime_dir: str | Path, run_id: str) -> bool:
    """读取持久化取消意图；不存在控制库时视为未请求。"""
    return _control_flags(runtime_dir, run_id)[1]


__all__ = ["cancellation_requested", "pause_requested"]
