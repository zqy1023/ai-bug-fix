"""原子写文件：临时文件 + 替换目标，处理 Windows 文件占用。

Windows 上 ``os.replace`` 对**正被其他进程读取**的目标文件会直接抛
PermissionError（WinError 5）——工作台正在读 ``final.json`` / ``steps.json``
时，写入就会失败。文件占用是瞬时的，重试即可成功。

这个坑在记录存储（``steps.json``）上已经踩过并修过，但终态报告写入
（``workflow/engine/graph.py``）没有共享那份逻辑，批次结果因此整份丢失。
这里把已验证的重试策略收敛成唯一实现。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

# 重试次数与退避上限：与记录存储此前验证过的口径一致。
REPLACE_RETRIES = 8
REPLACE_RETRY_BASE = 0.01
REPLACE_RETRY_CAP = 0.1


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """把文本原子写入 ``path``：先写临时文件，再替换目标。

    目标文件被其他进程占用时做有界指数退避重试；仍失败则**直接覆盖写入**，
    至少不丢内容（临时文件保留，便于事后核对与人工重放）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding=encoding) as file_obj:
        file_obj.write(text)
        file_obj.flush()
        os.fsync(file_obj.fileno())
    delay = REPLACE_RETRY_BASE
    last_error: PermissionError | None = None
    for attempt in range(REPLACE_RETRIES):
        try:
            temporary.replace(path)
            return
        except PermissionError as error:
            # 对端的读句柄没有共享删除，rename 会失败；读侧已缩短持有时长。
            last_error = error
            if attempt + 1 >= REPLACE_RETRIES:
                break
            time.sleep(delay)
            delay = min(delay * 2, REPLACE_RETRY_CAP)
    # 仍被占用：直接覆盖。内容可能不完整，但比整份丢失好。
    try:
        path.write_text(text, encoding=encoding)
    except OSError as error:
        # 连直接覆盖都失败（权限/磁盘）：临时文件仍在，可事后人工重放。
        raise error from last_error


def atomic_write_json(path: Path, value: object, *, indent: int = 2) -> None:
    """把对象原子写成 JSON（``ensure_ascii=False``，与项目内其他产物一致）。"""
    import json

    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=indent))


__all__ = [
    "REPLACE_RETRIES",
    "REPLACE_RETRY_BASE",
    "REPLACE_RETRY_CAP",
    "atomic_write_json",
    "atomic_write_text",
]
