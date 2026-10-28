"""准生产回归适配器：在独立 Git 工作区执行。

回归环境从源仓库本地克隆，带完整 Git 历史，再只覆盖本轮声明改过的文件。
命令只允许测试：不能提交、修改本轮代码或留下新的工作区文件。
"""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from git import Repo

from core.errors import StepFailed
from infrastructure.workspace.commands import run_fixed_commands
from infrastructure.workspace.safe_cleanup import remove_tree
from infrastructure.workspace.workspace import WorkspaceError, WorkspaceManager
from infrastructure.storage.redaction import public_exception
from agents.review.gates.run_regression.environment import prepare_regression_environment
from config.settings import settings


class RegressionRunner:
    """在独立 Git 工作区跑固定命令，并确认测试没有产生副作用。"""

    def __init__(
        self,
        workspace_manager: WorkspaceManager,
        commands: Sequence[Sequence[str]],
        source_repo: str | Path | None = None,
        timeout_seconds: int = 600,
        request_executor: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self._workspaces = workspace_manager
        self._commands = [list(command) for command in commands]
        self._source_repo = source_repo
        self._timeout_seconds = max(1, int(timeout_seconds))
        self._request_executor = request_executor

    def run(self, state: Mapping[str, Any]) -> dict[str, Any]:
        ai_workspace = state.get("ai_workspace_path")
        carrier = state.get("carrier")
        source_repo = state.get("source_repo_path") or self._source_repo
        if not ai_workspace or not carrier:
            return {"passed": False, "mode": "regression", "reason": "缺少 AI 工作区或 carrier"}
        if not self._commands and self._request_executor is None:
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
                cleanup_failed = False
                if stale_path is not None:
                    cleanup_failed = not remove_tree(stale_path)
                try:
                    workspace = self._workspaces.create_regression_workspace(
                        source_repo, task_key, ai_workspace, changed
                    )
                except WorkspaceError as retry_error:
                    if stale_path is None or cleanup_failed or "已存在" in str(retry_error):
                        raise WorkspaceError(
                            f"{retry_error}；可能有残留回归工作区需手工清理"
                        ) from retry_error
                    raise
            project = self._workspaces.project_root(workspace)
            environment = prepare_regression_environment(project) if self._request_executor is not None else {}
            repo = Repo(workspace)
            before = str(repo.head.commit.hexsha)
            status_before = repo.git.status(
                "--porcelain", "--untracked-files=all", "--ignored=matching"
            )
            hashes_before = _changed_hashes(project, changed)
            request_result = (
                self._request_executor(project, dict(state.get("normalized_log") or {}), carrier=str(carrier))
                if self._request_executor is not None else {}
            )
            commands_to_run = _commands_for(self._commands, changed, project)
            if commands_to_run:
                result = run_fixed_commands(project, commands_to_run, timeout_seconds=self._timeout_seconds)
            else:
                # 全部命令都是"仅在有 .py 改动时执行"的语法检查且本轮没有 .py 改动：
                # 没有需要执行的命令，按通过处理，而不是把缺参的原始命令塞回去假失败。
                result = {"passed": True, "results": [], "reason": "没有需要执行的回归命令"}
            if request_result:
                result["environment"] = environment
                result["request_regression"] = request_result
                result["results"] = request_result.get("commands", []) + result.get("results", [])
                result["passed"] = result.get("passed") is True and (
                    request_result.get("passed") is True or request_result.get("status") == "unverified"
                )
                if not request_result.get("passed"):
                    result["reason"] = request_result.get("reason") or "真实业务请求未通过"
            after = str(repo.head.commit.hexsha)
            status_after = repo.git.status(
                "--porcelain", "--untracked-files=all", "--ignored=matching"
            )
            if self._request_executor is not None:
                # main 初始化日志与 Nacos 本地缓存是入口的正常行为。仅豁免两个
                # 已被 Git 忽略的运行目录；受跟踪文件、源码和其它新文件仍须对账。
                status_before, _ = _runtime_status(status_before, workspace, project)
                status_after, runtime_artifacts = _runtime_status(status_after, workspace, project)
                result["runtime_artifacts"] = runtime_artifacts
            hashes_after = _changed_hashes(project, changed)
        except WorkspaceError as error:
            reason = public_exception(error, prefix="回归环境建立失败")
            if "可能有残留回归工作区需手工清理" in str(error):
                reason += "；可能有残留回归工作区需手工清理"
            failure = {"passed": False, "mode": "regression", "failure_kind": "execution", "reason": reason}
        except StepFailed:
            # 业务失败（如 PROXY_MANAGER_ADDRESS 未配置）自带 result 分类，
            # 不能被兜底 except 压成 execution 失败。
            raise
        except Exception as error:
            failure = {"passed": False, "mode": "regression", "failure_kind": "execution", "reason": public_exception(error, prefix="回归环境执行失败")}
        finally:
            if repo is not None:
                try:
                    repo.close()
                except Exception:
                    pass
            if workspace is not None:
                # 临时工作区清理失败（包括环境删除守卫抛 SystemExit）不能杀死
                # 工作流线程，也不能覆盖已经得到的回归业务结论。
                if not remove_tree(workspace):
                    result["cleanup_error"] = "回归工作区清理失败，已交由启动回收处理"
        if failure is not None:
            if result:
                failure["regression"] = result
            return failure
        if not result.get("passed"):
            return {"passed": False, "mode": "regression", "regression": result, "reason": result.get("reason") or "准生产回归命令执行失败"}
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
            # 只报"修改了工作区"无法排查：把新增/消失的 Git 状态行和哈希变化文件
            # 一并带出，交付报告和重试反馈才能定位是哪条命令产生的副作用。
            added = sorted(set(status_after.splitlines()) - set(status_before.splitlines()))
            removed = sorted(set(status_before.splitlines()) - set(status_after.splitlines()))
            hash_changes = sorted(
                key
                for key in set(hashes_before) | set(hashes_after)
                if hashes_before.get(key) != hashes_after.get(key)
            )
            return {
                "passed": False, "mode": "regression",
                "regression": {
                    **result,
                    "passed": False,
                    "reason": "准生产回归命令修改了工作区文件",
                    "workspace_changes": {
                        "status_added": added[:20],
                        "status_removed": removed[:20],
                        "hashes_changed": hash_changes[:20],
                    },
                },
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


def _runtime_status(status: str, workspace: Path, project: Path) -> tuple[str, list[str]]:
    """仅从状态对账中剔除明确的、被 Git 忽略的入口运行目录。

    main 初始化日志与 Nacos 本地缓存是入口的正常行为。豁免目录可用
    ``REGRESSION_RUNTIME_EXEMPT_DIRS``（逗号分隔）覆盖，默认 ``logs,nacos-data``；
    匹配按"Git 状态行路径以豁免目录为前缀"判定，不再要求整行逐字符相等
    （``--untracked-files=all`` 会把豁免目录里的单个文件逐行列出），但保留
    ``!!`` untracked/ignored 语义：受跟踪文件和其它新文件仍须对账。
    """
    prefix = project.relative_to(workspace).as_posix()
    prefix = "" if prefix == "." else prefix + "/"
    from config.nacos import nacos_get
    from config.settings import settings
    configured = nacos_get("REGRESSION_RUNTIME_EXEMPT_DIRS", "logs,nacos-data")
    exempt_dirs = {name.strip() for name in configured.split(",") if name.strip()} or {"logs", "nacos-data"}
    exempt_prefixes = tuple(f"!! {prefix}{name}/" for name in exempt_dirs)
    lines = status.splitlines()
    kept = [line for line in lines if not line.startswith(exempt_prefixes)]
    runtime = [line[3:] for line in lines if line.startswith(exempt_prefixes)]
    return "\n".join(kept), runtime


def _commands_for(
    commands: Sequence[Sequence[str]], changed_files: list[str], project: Path | None = None,
) -> list[list[str]]:
    """语法检查命令补上本轮改动文件。其它固定命令保持原样。

    本轮没有 .py 改动时，以 ``py_compile`` 结尾的命令整体跳过（返回空列表表示
    没有需要执行的命令）；不能把缺文件参数的原始命令塞回去，那必然执行失败。
    """
    files = [item for item in changed_files if item.endswith(".py") and (project is None or (project / item).is_file())]
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
