"""临时目录安全清理。

环境级删除守卫（例如 IDE 的批量删除保护）会在 ``shutil.rmtree`` 中抛
``SystemExit``；这个异常继承自 ``BaseException``，会穿透步骤的 ``except Exception``
并杀死工作流线程，留下永远 running 的步骤记录。临时目录属于可重建缓存，
清理失败只应记日志，不能影响业务执行。
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


def remove_tree(path: str | Path) -> bool:
    """删除目录或文件；只读对象先 chmod，失败返回 False。"""
    target = Path(path)
    if not target.exists() and not target.is_symlink():
        return True

    def onerror(function, failed_path, exc_info):
        try:
            os.chmod(failed_path, stat.S_IWRITE)
            function(failed_path)
        except OSError:
            raise exc_info[1]

    try:
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(target, onerror=onerror)
        return True
    except (OSError, SystemExit):
        logger.warning("临时目录清理失败，交由启动回收处理: %s", target, exc_info=True)
        return False


@contextmanager
def temporary_directory(prefix: str = "tmp-", parent: str | Path | None = None) -> Iterator[Path]:
    """创建临时目录，退出时安全删除（清理失败不抛异常）。"""
    path = Path(tempfile.mkdtemp(prefix=prefix, dir=str(parent) if parent else None))
    try:
        yield path
    finally:
        remove_tree(path)


__all__ = ["remove_tree", "temporary_directory"]
