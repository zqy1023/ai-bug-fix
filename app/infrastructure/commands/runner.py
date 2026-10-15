"""独立回归验证命令执行器。"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def run_fixed_commands(
    workspace: str | Path,
    commands: Sequence[Sequence[str]],
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    """只执行工作流预先配置的命令，禁止 AI 选择或拼接命令。

    原样返回 stdout/stderr：脱敏与截断属于"报告怎么留存"的决定，由 run_regression
    统一做（先脱敏再截断），这里不做取舍。
    """
    root = Path(workspace).resolve()
    results: list[dict[str, Any]] = []
    for command in commands:
        completed = subprocess.run(
            list(command),
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            shell=False,
        )
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

