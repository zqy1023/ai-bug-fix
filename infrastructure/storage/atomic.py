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
from uuid import uuid4
from pathlib import Path

# 重试次数与退避上限：与记录存储此前验证过的口径一致。
REPLACE_RETRIES = 8
REPLACE_RETRY_BASE = 0.01
REPLACE_RETRY_CAP = 0.1


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """把文本原子写入 ``path``：先写临时文件，再替换目标。

    目标文件被占用时有界重试。失败保留旧目标和唯一临时文件，绝不截断旧版本。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
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
    raise PermissionError(
        f"原子替换失败，旧目标保持不变；完整候选保留于 {temporary}"
    ) from last_error


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
