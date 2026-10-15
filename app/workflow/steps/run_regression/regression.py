"""准生产回归适配器：在独立 Git 工作区执行。

回归环境从源仓库本地克隆，带完整 Git 历史，再只覆盖本轮声明改过的文件。
命令只允许测试：不能提交、修改本轮代码或留下新的工作区文件。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.infrastructure.commands.runner import run_fixed_commands
from app.infrastructure.filesystem.workspace import WorkspaceError, WorkspaceManager
from git import Repo


def _force_remove(func: Any, path: str, _exc_info: Any) -> None:
    os.chmod(path, stat.S_IWRITE)
    func(path)


class RegressionRunner:
    """在独立 Git 工作区跑固定命令，并确认测试没有产生副作用。"""

    def __init__(
        self,
        workspace_manager: WorkspaceManager,
        commands: Sequence[Sequence[str]],
        source_repo: str | Path | None = None,
    ) -> None:
        self._workspaces = workspace_manager
        self._commands = [list(command) for command in commands]
        self._source_repo = source_repo

    def run(self, state: Mapping[str, Any]) -> dict[str, Any]:
        ai_workspace = state.get("ai_workspace_path")
        carrier = state.get("carrier")
        source_repo = state.get("source_repo_path") or self._source_repo
        if not ai_workspace or not carrier:
            return {"passed": False, "reason": "缺少 AI 工作区或 carrier"}
        if not self._commands:
            return {"passed": False, "reason": "未配置固定准生产回归命令", "regression": {}}
        if not source_repo:
            return {"passed": False, "reason": "源仓库未配置，无法建立独立回归环境"}

        changed = [str(item) for item in state.get("changed_files") or []]
        task_key = str(state.get("task_key") or state.get("task_id") or carrier)
        workspace: Path | None = None
        repo: Repo | None = None
        result: dict[str, Any] = {}
        before = after = ""
        status_before = status_after = ""
        hashes_before: dict[str, str] = {}
        hashes_after: dict[str, str] = {}
        failure: dict[str, Any] | None = None
        try:
            workspace = self._workspaces.create_regression_workspace(
                source_repo, task_key, ai_workspace, changed
            )
            project = self._workspaces.project_root(workspace)
            repo = Repo(workspace)
            before = str(repo.head.commit.hexsha)
            status_before = repo.git.status("--porcelain", "--untracked-files=all")
            hashes_before = _changed_hashes(project, changed)
            result = run_fixed_commands(project, _commands_for(self._commands, changed))
            after = str(repo.head.commit.hexsha)
            status_after = repo.git.status("--porcelain", "--untracked-files=all")
            hashes_after = _changed_hashes(project, changed)
        except WorkspaceError as error:
            failure = {"passed": False, "reason": f"回归环境建立失败: {error}"}
        except Exception as error:
            failure = {"passed": False, "reason": f"回归环境执行失败: {error}"}
        finally:
            if repo is not None:
                repo.close()
            if workspace is not None:
                try:
                    shutil.rmtree(workspace)
                except OSError:
                    shutil.rmtree(workspace, onerror=_force_remove)
        if failure is not None:
            return failure
        if not result.get("passed"):
            return {"passed": False, "regression": result, "reason": "准生产回归命令执行失败"}
        if before != after:
            return {
                "passed": False,
                "regression": {
                    **result, "passed": False, "reason": "准生产回归命令产生了提交",
                    "head_before": before, "head_after": after,
                },
                "reason": "准生产回归命令违反约束，产生了提交",
            }
        if status_before != status_after or hashes_before != hashes_after:
            return {
                "passed": False,
                "regression": {**result, "passed": False, "reason": "准生产回归命令修改了工作区文件"},
                "reason": "准生产回归命令必须只测试，不得改变代码或生成未跟踪文件",
            }
        return {"passed": True, "regression": result, "reason": ""}


def _changed_hashes(project: Path, changed_files: list[str]) -> dict[str, str]:
    """记录本轮修改文件的哈希，发现测试命令偷偷改写代码。"""
    result: dict[str, str] = {}
    root = project.resolve()
    for relative in changed_files:
        target = (root / relative).resolve(strict=False)
        try:
            target.relative_to(root)
        except ValueError:
            result[relative] = "PATH_ESCAPE"
            continue
        if target.is_file() and not target.is_symlink():
            result[relative] = hashlib.sha256(target.read_bytes()).hexdigest()
        else:
            result[relative] = "MISSING"
    return result


def _commands_for(commands: Sequence[Sequence[str]], changed_files: list[str]) -> list[list[str]]:
    """语法检查命令补上本轮改动文件。其它固定命令保持原样。"""
    files = [item for item in changed_files if item.endswith(".py")]
    resolved: list[list[str]] = []
    for command in commands:
        items = list(command)
        if items and items[0] == "python":
            items[0] = sys.executable
        if items and items[-1] == "py_compile":
            if not files:
                continue
            items.extend(files)
        resolved.append(items)
    return resolved or [list(command) for command in commands]


__all__ = ["RegressionRunner"]
