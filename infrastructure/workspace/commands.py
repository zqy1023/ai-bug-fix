"""独立回归验证命令执行器。"""

from __future__ import annotations

import os
import signal
import subprocess
import psutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from infrastructure.workspace.runtime_environment import runtime_temporary_directory


def _stop_process_tree(process: subprocess.Popen) -> None:
    if os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return
    # 先暂停所属父进程，再逐层暂停后代，避免枚举过程中继续产生子进程。
    # psutil 的进程身份校验避免将已复用的 PID 当成本次进程终止。
    owned: list[psutil.Process] = []
    try:
        root = psutil.Process(process.pid)
        root.suspend()
        owned.append(root)
        index = 0
        while index < len(owned):
            parent = owned[index]
            index += 1
            try:
                children = parent.children()
            except psutil.NoSuchProcess:
                continue
            for child in children:
                try:
                    child.suspend()
                    owned.append(child)
                except psutil.NoSuchProcess:
                    pass
    except psutil.NoSuchProcess:
        pass
    finally:
        for child in reversed(owned):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(owned, timeout=10)


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
    with runtime_temporary_directory(prefix="ai-bug-fixer-pycache-") as pycache:
        env = {
            **os.environ,
            "PYTHONPYCACHEPREFIX": pycache,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
            "AI_REPAIR_RUNTIME_STATE_DIR": str(Path(pycache) / "runtime-state"),
        }
        # 外部 TLS 调试路径不属于验证环境，不能让其阻断导入或写入用户目录。
        env.pop("SSLKEYLOGFILE", None)
        for command in commands:
            try:
                completed = subprocess.Popen(
                    list(command),
                    cwd=root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    shell=False,
                    env=env,
                    start_new_session=os.name != "nt",
                )
                stdout, stderr = completed.communicate(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                # venv launcher 的真实解释器是子进程；须在父进程仍在时回收整棵树。
                _stop_process_tree(completed)
                stdout, stderr = completed.communicate(timeout=15)
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
                "stdout": stdout,
                "stderr": stderr,
                "passed": completed.returncode == 0,
            }
            results.append(result)
            if completed.returncode != 0:
                return {"passed": False, "results": results, "reason": "固定验证命令失败"}
    return {"passed": True, "results": results}

