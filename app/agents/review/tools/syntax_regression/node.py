"""在临时 pycache 中编译本轮 Python 变更。"""

from __future__ import annotations

import hashlib
import py_compile
from pathlib import Path

from app.application.context import StepContext
from app.domain.errors import StepFailed
from app.infrastructure.filesystem.safe_cleanup import temporary_directory
from app.agents.review.tools.syntax_regression.models import SyntaxInput, SyntaxOutput


def run(ctx: StepContext, data: SyntaxInput) -> SyntaxOutput:
    workspace = Path(data.ai_workspace_path).resolve()
    if not workspace.is_dir():
        raise StepFailed("语法回归工作区不存在", {"passed": False, "mode": "syntax"})
    files = [item for item in data.changed_files if item.endswith(".py")]
    if not files:
        return SyntaxOutput(
            passed=True, reason="没有适用的 Python 文件", checked_files=[]
        )
    with temporary_directory("ai-syntax-pycache-") as pycache:
        checked: list[str] = []
        for relative in files:
            target = (workspace / relative).resolve()
            try:
                target.relative_to(workspace)
            except ValueError:
                raise StepFailed(
                    "语法回归文件超出工作区", {"passed": False, "mode": "syntax"}
                )
            if not target.is_file():
                if (
                    relative in data.baseline_manifest
                    and relative in data.deleted_files
                    and not target.exists()
                ):
                    continue
                raise StepFailed(
                    f"语法回归文件不存在: {relative}",
                    {"passed": False, "mode": "syntax"},
                )
            try:
                # pycache 文件名用相对路径的确定性哈希：hash() 每个进程随机化，
                # 跨进程/跨运行不可复现，也不利于排查残留缓存。
                digest = hashlib.sha256(str(relative).encode("utf-8")).hexdigest()[:16]
                py_compile.compile(
                    str(target),
                    cfile=str(Path(pycache) / f"{digest}.pyc"),
                    doraise=True,
                )
                checked.append(relative)
            except py_compile.PyCompileError as error:
                raise StepFailed(
                    f"语法检查失败: {relative}: {error.msg}",
                    {"passed": False, "mode": "syntax", "file": relative},
                    code="SYNTAX_INVALID",
                ) from error
    return SyntaxOutput(
        passed=True,
        reason=f"已编译 {len(checked)} 个 Python 文件",
        checked_files=checked,
    )
