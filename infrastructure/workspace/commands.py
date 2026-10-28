"""独立回归验证命令执行器。"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from config.settings import settings


def _decode_timeout_output(value: Any) -> str:
    """安全解码超时异常中可能仍为 bytes 的输出。"""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def run_fixed_commands(
    workspace: str | Path,
    commands: Sequence[Sequence[str]],
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    """只执行工作流预先配置的命令，禁止 AI 选择或拼接命令。

    原样返回 stdout/stderr，调用方决定摘要和归档方式。
    """
    root = Path(workspace).resolve()
    results: list[dict[str, Any]] = []
    # ``python -m py_compile`` 会写 __pycache__，PYTHONDONTWRITEBYTECODE=1 也拦不住
    # 显式编译。字节码缓存必须重定向到工作区外的临时目录，否则回归门禁会把正常的
    # 编译产物误判成"修改了工作区文件"。
    with tempfile.TemporaryDirectory(prefix="ai-bug-fixer-pycache-") as pycache:
        env = {
            **os.environ,
            "PYTHONPYCACHEPREFIX": pycache,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        }
        for command in commands:
            try:
                completed = subprocess.run(
                    list(command),
                    cwd=root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds,
                    shell=False,
                    env=env,
                )
            except subprocess.TimeoutExpired as error:
                # TimeoutExpired 的 output/stderr 在 text=True 时仍可能是 bytes。
                stdout = _decode_timeout_output(
                    getattr(error, "stdout", None) or getattr(error, "output", None)
                )
                stderr = _decode_timeout_output(getattr(error, "stderr", None))
                results.append(
                    {
                        "command": list(command),
                        "returncode": None,
                        "stdout": stdout,
                        "stderr": stderr,
                        "passed": False,
                    }
                )
                return {
                    "passed": False,
                    "results": results,
                    "reason": f"命令超时: {' '.join(command)}",
                }
            result = {
                "command": list(command),
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "passed": completed.returncode == 0,
            }
            results.append(result)
            if completed.returncode != 0:
                return {"passed": False, "results": results, "reason": "固定验证命令失败"}
    return {"passed": True, "results": results}

