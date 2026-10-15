"""准生产回归适配器：在独立的 Git 工作区执行。

回归环境从源仓库本地克隆，带完整 Git 历史，再只覆盖本轮声明改过的文件。
命令在这个工作区里跑，跑完比对 HEAD：回归命令不得产生提交。
该工作区与 AI 隔离区、开发人员正在使用的源仓库都不是同一个目录，
命令失败或违规时整体删除，不留残留。
"""

from __future__ import annotations

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
    """Windows 上 .git 目录的只读文件需要先改权限才能删。"""
    os.chmod(path, stat.S_IWRITE)
    func(path)


class RegressionRunner:
    """在独立 Git 工作区跑固定命令，并校验命令没有提交代码。"""

    def __init__(
        self,
        workspace_manager: WorkspaceManager,
        commands: Sequence[Sequence[str]],
        source_repo: str | Path | None = None,
    ) -> None:
        """注入工作区管理器、固定验证命令和回归基线所在的源仓库。"""
        self._workspaces = workspace_manager
        self._commands = [list(command) for command in commands]
        self._source_repo = source_repo

    def run(self, state: Mapping[str, Any]) -> dict[str, Any]:
        """克隆回归工作区、执行固定命令，并确认 HEAD 没有前进。"""
        ai_workspace = state.get("ai_workspace_path")
        carrier = state.get("carrier")
        source_repo = state.get("source_repo_path") or self._source_repo
        if not ai_workspace or not carrier:
            return {"passed": False, "reason": "缺少 AI 工作区或 carrier"}
        # 命令缺失比源仓库缺失更先报：没有命令时根本不该去建回归环境。
        if not self._commands:
            return {"passed": False, "reason": "未配置固定准生产回归命令", "regression": {}}
        if not source_repo:
            return {"passed": False, "reason": "源仓库未配置，无法建立独立回归环境"}

        changed = [str(item) for item in state.get("changed_files") or []]
        task_key = str(state.get("task_key") or state.get("task_id") or carrier)
        workspace: Path | None = None
        repo: Repo | None = None
        result: dict[str, Any] = {}
        before = ""
        after = ""
        failure: dict[str, Any] | None = None
        try:
            workspace = self._workspaces.create_regression_workspace(
                source_repo, task_key, ai_workspace, changed
            )
            project = self._workspaces.project_root(workspace)
            repo = Repo(workspace)
            before = str(repo.head.commit.hexsha)
            result = run_fixed_commands(project, _commands_for(self._commands, changed))
            after = str(repo.head.commit.hexsha)
        except WorkspaceError as error:
            failure = {"passed": False, "reason": f"回归环境建立失败: {error}"}
        except Exception as error:
            failure = {"passed": False, "reason": f"回归环境执行失败: {error}"}
        finally:
            # 先关 GitPython 的句柄，否则 Windows 上删不掉 .git 目录。
            if repo is not None:
                repo.close()
            # 无论命令成功、失败还是违规，回归克隆都要删掉，不能留在运行目录里。
            if workspace is not None:
                try:
                    shutil.rmtree(workspace)
                except OSError:
                    # Windows 上 .git 内的只读文件可能需要权限回调才能删掉。
                    shutil.rmtree(workspace, onerror=_force_remove)
        if failure is not None:
            return failure

        if not result.get("passed"):
            return {
                "passed": False,
                "regression": result,
                "reason": "准生产回归命令执行失败",
            }
        if before != after:
            return {
                "passed": False,
                "regression": {
                    **result,
                    "passed": False,
                    "reason": "准生产回归命令产生了提交",
                    "head_before": before,
                    "head_after": after,
                },
                "reason": "准生产回归命令违反约束，产生了提交",
            }
        return {"passed": True, "regression": result, "reason": ""}


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
