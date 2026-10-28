"""跨线程、跨进程协调共享源码缓存的发布、引用与回收。"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_LOCK = threading.RLock()


@contextmanager
def shared_cache_lock(root: Path) -> Iterator[None]:
    """持有共享根目录锁；进程退出由操作系统自动释放。"""
    root.mkdir(parents=True, exist_ok=True)
    with _LOCK, (root / ".cache.lock").open("a+b") as handle:
        locked = False
        try:
            if sys.platform == "win32":
                import msvcrt

                if handle.seek(0, 2) == 0:
                    handle.write(b"0")
                    handle.flush()
                deadline = time.monotonic() + 300
                while not locked:
                    handle.seek(0)
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        locked = True
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("等待共享源码缓存锁超时")
                        time.sleep(0.1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                locked = True
            yield
        finally:
            if locked:
                if sys.platform == "win32":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
