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
from app.infrastructure.redaction import public_exception
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
            return {"passed": False, "mode": "regression", "reason": "缺少 AI 工作区或 carrier"}
        if not self._commands:
            return {"passed": False, "mode": "regression", "reason": "未配置固定准生产回归命令", "regression": {}}
        if not source_repo:
            return {"passed": False, "mode": "regression", "reason": "源仓库未配置，无法建立独立回归环境"}

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
            try:
                workspace = self._workspaces.create_regression_workspace(
                    source_repo, task_key, ai_workspace, changed
                )
            except WorkspaceError as error:
                # 上次克隆后崩溃可能留下同名目录；清理后只重试一次，避免覆盖未知目录。
                error_text = str(error)
                if "已存在" not in error_text:
                    raise
                marker = "拒绝覆盖: "
                stale_path = (
                    Path(error_text.rsplit(marker, 1)[-1]).resolve()
                    if marker in error_text
                    else None
                )
                cleanup_error: OSError | None = None
                if stale_path is not None:
                    try:
                        shutil.rmtree(stale_path, onerror=_force_remove)
                    except OSError as stale_error:
                        cleanup_error = stale_error
                try:
                    workspace = self._workspaces.create_regression_workspace(
                        source_repo, task_key, ai_workspace, changed
                    )
                except WorkspaceError as retry_error:
                    if stale_path is None or cleanup_error is not None or "已存在" in str(retry_error):
                        raise WorkspaceError(
                            f"{retry_error}；可能有残留回归工作区需手工清理"
                        ) from retry_error
                    raise
            project = self._workspaces.project_root(workspace)
            repo = Repo(workspace)
            before = str(repo.head.commit.hexsha)
            status_before = repo.git.status(
                "--porcelain", "--untracked-files=all", "--ignored=matching"
            )
            hashes_before = _changed_hashes(project, changed)
            commands_to_run = _commands_for(self._commands, changed)
            if commands_to_run:
                result = run_fixed_commands(project, commands_to_run)
            else:
                # 全部命令都是"仅在有 .py 改动时执行"的语法检查且本轮没有 .py 改动：
                # 没有需要执行的命令，按通过处理，而不是把缺参的原始命令塞回去假失败。
                result = {"passed": True, "results": [], "reason": "没有需要执行的回归命令"}
            after = str(repo.head.commit.hexsha)
            status_after = repo.git.status(
                "--porcelain", "--untracked-files=all", "--ignored=matching"
            )
            hashes_after = _changed_hashes(project, changed)
        except WorkspaceError as error:
            reason = public_exception(error, prefix="回归环境建立失败")
            if "可能有残留回归工作区需手工清理" in str(error):
                reason += "；可能有残留回归工作区需手工清理"
            failure = {"passed": False, "mode": "regression", "reason": reason}
        except Exception as error:
            failure = {"passed": False, "mode": "regression", "reason": public_exception(error, prefix="回归环境执行失败")}
        finally:
            try:
                if repo is not None:
                    repo.close()
                if workspace is not None:
                    try:
                        shutil.rmtree(workspace)
                    except OSError:
                        # Windows 上可能需要先去掉只读属性再清理。
                        shutil.rmtree(workspace, onerror=_force_remove)
            except OSError as error:
                # 清理失败不能覆盖已经得到的回归业务结论。
                result["cleanup_error"] = f"回归工作区清理失败: {error}"
        if failure is not None:
            if result:
                failure["regression"] = result
            return failure
        if not result.get("passed"):
            return {"passed": False, "mode": "regression", "regression": result, "reason": "准生产回归命令执行失败"}
        if before != after:
            return {
                "passed": False, "mode": "regression",
                "regression": {
                    **result, "passed": False, "reason": "准生产回归命令产生了提交",
                    "head_before": before, "head_after": after,
                },
                "reason": "准生产回归命令违反约束，产生了提交",
            }
        if status_before != status_after or hashes_before != hashes_after:
            return {
                "passed": False, "mode": "regression",
                "regression": {**result, "passed": False, "reason": "准生产回归命令修改了工作区文件"},
                "reason": "准生产回归命令必须只测试，不得改变代码或生成未跟踪文件",
            }
        return {"passed": True, "mode": "regression", "regression": result, "reason": ""}


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
    """语法检查命令补上本轮改动文件。其它固定命令保持原样。

    本轮没有 .py 改动时，以 ``py_compile`` 结尾的命令整体跳过（返回空列表表示
    没有需要执行的命令）；不能把缺文件参数的原始命令塞回去，那必然执行失败。
    """
    files = [item for item in changed_files if item.endswith(".py")]
    resolved: list[list[str]] = []
    for command in commands:
        items = list(command)
        if items and (
            items[0] in {"python", "python3"}
            or items[0].casefold() in {"python.exe", "python3.exe"}
        ):
            items[0] = sys.executable
        if items and items[-1] == "py_compile":
            if not files:
                continue
            items.extend(files)
        resolved.append(items)
    return resolved


__all__ = ["RegressionRunner"]
