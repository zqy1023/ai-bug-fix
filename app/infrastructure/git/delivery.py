"""修复分支交付适配器。

修复全程发生在无 Git 的 AI 隔离区里。审查通过后，交付器从源仓库的明确基准
提交克隆出一个**独立工作区**，在里面创建修复分支、复制本轮文件、提交并按配置
推送。开发人员正在使用的源仓库全程不被检出、不被改写；失败时删除临时工作区。
模块没有 MR 或自动合并能力。
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.infrastructure.filesystem.workspace import WorkspaceError, WorkspaceManager
from app.infrastructure.redaction import mask_secrets, redact_output
from app.domain.errors import DeliveryError
from git import Repo
from config.settings import settings


class DeliveryPort(Protocol):
    """修复分支交付能力的最小接口。"""

    def deliver(self, state: Mapping[str, Any]) -> dict[str, Any]:
        """创建分支并交付本轮代码。"""

    def discard_branch(self, state: Mapping[str, Any]) -> dict[str, Any]:
        """删除最终审批拒绝的修复分支。"""


# 提交说明会永久留在版本库里，比运行记录更难回收，因此对随单变化的内容脱敏。
# 单号、运单号这类连续数字是最容易泄露又最没信息量的部分。敏感键名的掩码规则
# 与运行记录共用 ``app.infrastructure.redaction.mask_secrets``。
_LONG_NUMBER = re.compile(r"\d{8,}")
_MAX_COMMENT_CHARS = settings.max_commit_comment_chars
_MAX_AUDIT_BYTES = 512 * 1024
_logger = logging.getLogger(__name__)


def _clone_repo(git_root: Path, target: Path) -> Repo:
    """克隆源仓库。优先硬链接，跨盘或权限不足时退回普通复制。

    ``local=True`` 在同盘能省一次对象复制，但 Windows 上硬链接失败会直接
    中断整个克隆（``failed to create link: Improper link``）。
    """
    try:
        return Repo.clone_from(str(git_root), str(target), local=True)
    except Exception as local_error:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        try:
            return Repo.clone_from(str(git_root), str(target))
        except Exception:
            raise local_error from None


def _one_line(value: Any, limit: int = _MAX_COMMENT_CHARS) -> str:
    """把一段文本压成提交说明里的一行：脱敏、压空白、限长。"""
    text = str(value or "").strip()
    if not text:
        return ""
    text = mask_secrets(text)
    text = _LONG_NUMBER.sub("***", text)
    # 换行会把段落写乱，一行一段地压平；多空格也会让正文看起来参差不齐。
    text = " ".join(text.split())
    if len(text) > limit:
        text = f"{text[:limit]}…(已截断)"
    return text


@dataclass(frozen=True)
class GitDeliveryConfig:
    """Git 交付的显式运行配置。"""

    # remote_name：用于比较分支和推送的 Git 远程名称。
    remote_name: str = settings.default_git_remote
    # push：是否将已提交的修复分支推送到远端。
    push: bool = True
    # source_repo：包含 Git 元数据的源仓库路径。
    source_repo: Path | None = None
    # workspace_manager：负责隔离工作区路径与文件同步的管理器。
    workspace_manager: WorkspaceManager | None = None


class GitBranchDelivery:
    """在独立工作区中创建修复分支、复制、提交并推送修改。"""

    _BRANCH = settings.repair_branch_pattern
    _AUDIT_SUFFIXES = {".md", ".json"}

    def __init__(self, config: GitDeliveryConfig | None = None) -> None:
        """创建不执行 MR 或自动合并的交付器。"""
        self._config = config or GitDeliveryConfig()
        self._workspaces = self._config.workspace_manager or WorkspaceManager()

    def discard_branch(self, state: Mapping[str, Any]) -> dict[str, Any]:
        """最终审批拒绝时删除本任务已推送的修复分支，不触碰默认分支。"""
        branch = str(state.get("repair_branch") or "")
        source_repo = state.get("source_repo_path") or self._config.source_repo
        if not branch or not self._BRANCH.fullmatch(branch):
            raise DeliveryError("拒绝审批时缺少合法修复分支名")
        if not source_repo:
            raise DeliveryError("拒绝审批时缺少源仓库，无法清理修复分支")
        if not state.get("pushed"):
            return {"deleted": False, "reason": "branch_not_pushed"}
        if branch in {"main", "master", "develop"}:
            raise DeliveryError("拒绝删除受保护的默认分支")
        branch_root = self._repo_root(source_repo)
        try:
            repo = Repo(branch_root, search_parent_directories=True)
        except Exception as error:
            raise DeliveryError(f"无法打开源 Git 仓库以清理分支: {branch_root}") from error
        if self._config.remote_name not in {remote.name for remote in repo.remotes}:
            raise DeliveryError(f"远程不存在，无法删除被拒绝的分支: {self._config.remote_name}")
        try:
            repo.git.push(self._config.remote_name, "--delete", branch)
        except Exception as error:
            raise DeliveryError(f"删除被拒绝的远程修复分支失败: {branch}") from error
        return {"deleted": True, "repair_branch": branch}

    def deliver(self, state: Mapping[str, Any]) -> dict[str, Any]:
        """把 AI 工作区修改复制到独立修复分支工作区并提交推送。"""
        ai_workspace = state.get("ai_workspace_path")
        branch = state.get("repair_branch")
        source_repo = state.get("source_repo_path") or self._config.source_repo
        if not ai_workspace or not branch:
            raise DeliveryError("缺少 AI 工作区或修复分支名")
        if not self._BRANCH.fullmatch(str(branch)):
            raise DeliveryError("修复分支名不符合安全格式")

        # branch_root 是源仓库里的**业务目录**（可能位于 Git 根的子目录），
        # 只用来定位 Git 根和计算相对路径，交付过程不在这里检出或写文件。
        branch_root = self._repo_root(source_repo)
        source_root = Path(str(ai_workspace)).resolve()
        if branch_root == source_root:
            raise DeliveryError("AI 隔离区与源仓库指向同一目录，拒绝直接提交")

        try:
            repo = Repo(branch_root, search_parent_directories=True)
        except Exception as error:
            raise DeliveryError(f"不是有效的本地 Git 仓库: {branch_root}") from error
        git_root = Path(repo.working_tree_dir or branch_root).resolve()

        workspace: Path | None = None
        delivery_repo: Repo | None = None
        commit = None
        pushed = False
        report_files: list[str] = []
        try:
            workspace = self._delivery_workspace(git_root, str(branch))
            delivery_repo = Repo(workspace)
            delivery_project = self._workspaces.project_root(workspace)
            prefix = delivery_project.resolve().relative_to(workspace.resolve())
            relative_files = [
                str(item).replace("\\", "/") for item in (state.get("changed_files") or [])
            ]
            outside_carrier = self._outside_carrier(
                delivery_project, str(state.get("carrier", "")), relative_files
            )
            if outside_carrier:
                raise DeliveryError(
                    f"禁止提交目标 carrier 之外的文件: {sorted(outside_carrier)[0]}"
                )

            # 先切到明确基准再建分支，然后才复制文件。
            # 顺序反过来的话，未提交的新文件会让 checkout 因冲突失败。
            base = self._base_branch(repo, self._config.remote_name)
            if str(branch) in {head.name for head in delivery_repo.heads}:
                raise DeliveryError(
                    f"修复分支已存在，拒绝在旧分支上叠加新任务: {branch}",
                    status="GIT_BRANCH_EXISTS",
                    branch_workspace_path=str(workspace),
                )
            delivery_repo.git.checkout("-b", str(branch), base)

            changed_files = self._copy_changed_files(
                source_root,
                delivery_project,
                str(state.get("carrier", "")),
                state.get("changed_files", []),
                extra_allowed=outside_carrier,
            )
            report_files = self._copy_reports(
                source_root,
                delivery_project,
                state.get("report_paths", []),
                carrier=str(state.get("carrier") or ""),
            )
            audit_files = self._copy_audit_records(source_root, delivery_project, state)
            paths_to_add = sorted({*changed_files, *report_files, *audit_files})
            if not paths_to_add:
                raise DeliveryError("没有可提交的修复文件或报告")

            git_paths = [
                (prefix / Path(path)).as_posix() if prefix != Path(".") else path
                for path in paths_to_add
            ]
            if not delivery_repo.git.status("--porcelain", "--", *git_paths).strip():
                raise DeliveryError("修复分支没有待提交差异")
            try:
                # 新文件不在索引里时，直接 commit -- path 会报 pathspec did not match。
                # Windows 的 core.ignorecase 还会把 gathers/asccourier 收成已有的
                # gathers/ASCCourier；暂存清单必须不带原路径过滤，提交用 Git 收下的路径。
                delivery_repo.git.add("--", *git_paths)
                requested = {path.casefold() for path in git_paths}
                staged = [
                    line.replace("\\", "/")
                    for line in delivery_repo.git.diff("--cached", "--name-only").splitlines()
                    if line.replace("\\", "/").casefold() in requested
                ]
                if not staged:
                    raise DeliveryError(f"修复文件未能进入暂存区: {git_paths[0]}")
                delivery_repo.git.commit("-m", self._commit_message(state), "--", *staged)
                commit = delivery_repo.head.commit
            except Exception as error:
                raise DeliveryError(
                    f"修复分支提交失败: {branch}",
                    status="GIT_COMMIT_FAILED",
                    branch_workspace_path=str(workspace),
                ) from error

            pushed = False
            # allow_push 是本轮配置策略，require_push 是调用方门禁。
            # 两者分离后，本地交付可以没有 remote；明确要求推送时则必须同时允许推送。
            remote_names = {remote.name for remote in repo.remotes}
            allow_push = bool(state.get("allow_push", self._config.push))
            require_push = bool(state.get("require_push"))
            if require_push and not allow_push:
                raise DeliveryError("本次交付要求推送，但运行配置禁止远程推送")
            if require_push and self._config.remote_name not in remote_names:
                raise DeliveryError(
                    f"本次交付要求推送分支，但源仓库没有远程 {self._config.remote_name}"
                )
            if allow_push and self._config.remote_name in remote_names:
                # 提交对象只存在于临时交付克隆：本地克隆只硬链接既有对象，新提交对源仓库
                # 不可见。因此必须由克隆按源仓库配置的远程 URL 推送（HEAD 即刚建的分支），
                # 用源仓库的 Repo 按 SHA refspec 推送会报 bad object。
                try:
                    remote_url = repo.git.remote("get-url", self._config.remote_name).strip()
                    delivery_repo.git.push(remote_url, f"HEAD:refs/heads/{branch}")
                    pushed = True
                except Exception as error:
                    raise DeliveryError(
                        f"修复分支推送失败: {branch}",
                        status="GIT_PUSH_FAILED",
                        branch_workspace_path=str(git_root),
                        commit=commit.hexsha,
                    ) from error
        finally:
            try:
                if delivery_repo is not None:
                    delivery_repo.close()
            except Exception:
                pass
            if workspace is not None:
                shutil.rmtree(workspace, ignore_errors=True)

        if commit is None:  # pragma: no cover - 提交失败都在上面抛出
            raise DeliveryError("修复分支没有生成提交")
        return {
            "delivery_status": state.get("delivery_status"),
            "branch_workspace_path": str(git_root),
            "repair_branch": str(branch),
            "commit": commit.hexsha,
            "pushed": pushed,
            "report_paths": [str(git_root / path) for path in report_files],
            "merge_request_created": False,
            "auto_merged": False,
        }

    def _delivery_workspace(self, git_root: Path, branch: str) -> Path:
        """克隆一个只用于本次交付的临时工作区，不碰源仓库的检出。"""
        target = self._workspaces.allocate("delivery", branch)
        shutil.rmtree(target, ignore_errors=True)
        try:
            _clone_repo(git_root, target)
        except Exception as error:
            shutil.rmtree(target, ignore_errors=True)
            raise DeliveryError(f"交付工作区克隆失败: {git_root}") from error
        return target

    def _repo_root(self, source_repo: Any) -> Path:
        """定位源仓库中的**业务项目目录**。

        必须走 ``WorkspaceManager.project_root``：``AI_REPAIR_SOURCE_REPO`` 指向
        仓库根，而 ``gathers/``、``spiders/`` 位于 ``AI_REPAIR_PROJECT_SUBDIR``
        之下。直接拿仓库根去解析 carrier 会一路报"工作区缺少目标 carrier"。
        """
        if not source_repo:
            raise DeliveryError("源仓库未配置，无法交付修复分支")
        try:
            return self._workspaces.project_root(source_repo)
        except WorkspaceError as error:
            raise DeliveryError(str(error)) from error

    @staticmethod
    def _outside_carrier(project_root: Path, carrier: str, files: list[str]) -> set[str]:
        """挑出不在目标 carrier 目录内的路径，这些只能来自获批共享层方案。"""
        try:
            allowed = WorkspaceManager.resolve_carrier_path(project_root, carrier)
        except WorkspaceError:
            return set()
        outside: set[str] = set()
        for raw in files:
            candidate = (project_root / raw).resolve()
            try:
                candidate.relative_to(allowed)
            except ValueError:
                outside.add(raw)
        return outside

    @staticmethod
    def _base_branch(repo: Repo, remote_name: str = settings.default_git_remote) -> str:
        """返回修复分支应派生的基准提交 SHA。"""
        remotes = {remote.name for remote in repo.remotes}
        remote_refs = {
            ref.name
            for ref in repo.refs
            if ref.name.startswith(f"{remote_name}/")
        }
        base: str | None = None
        if remote_name in remotes:
            try:
                symbolic = str(
                    repo.git.symbolic_ref(
                        "--short", f"refs/remotes/{remote_name}/HEAD"
                    )
                ).strip()
                if symbolic in remote_refs:
                    base = symbolic
            except Exception:
                pass
            if base is None:
                for candidate in (
                    f"{remote_name}/main",
                    f"{remote_name}/master",
                ):
                    if candidate in remote_refs:
                        base = candidate
                        break
            if base is None:
                raise DeliveryError(
                    f"远程 {remote_name} 没有可用的默认跟踪分支"
                )
        else:
            try:
                heads = {head.name for head in repo.heads}
                for candidate in ("main", "master"):
                    if candidate in heads:
                        base = candidate
                        break
                if base is None:
                    base = repo.active_branch.name
            except Exception as error:
                raise DeliveryError("无法确定修复分支基准") from error
        try:
            return str(repo.git.rev_parse(base)).strip()
        except Exception as error:
            raise DeliveryError("无法确定修复分支基准") from error

    @classmethod
    def _copy_changed_files(
        cls,
        source_root: Path,
        destination_root: Path,
        carrier: str,
        raw_files: Any,
        extra_allowed: set[str] | None = None,
    ) -> list[str]:
        """只复制目标 carrier 的 changed_files，以及显式放行的获批共享层文件。"""
        if not isinstance(raw_files, list):
            return []
        root = destination_root.resolve()
        relative_files: list[tuple[Path, Path]] = []
        for raw_file in raw_files:
            relative = Path(str(raw_file).replace("\\", "/"))
            candidate = (root / relative).resolve()
            try:
                candidate.relative_to(root)
            except ValueError as error:
                raise DeliveryError(f"禁止提交工作区外的目标 carrier 文件: {raw_file}") from error
            relative_files.append((relative, candidate))
        try:
            allowed = WorkspaceManager.resolve_carrier_path(root, carrier)
        except WorkspaceError as error:
            raise DeliveryError(str(error)) from error
        allowed_extra = {str(item).replace("\\", "/") for item in (extra_allowed or set())}
        copied: list[str] = []
        for raw_file, (relative, candidate) in zip(raw_files, relative_files, strict=True):
            if relative.as_posix() not in allowed_extra:
                try:
                    candidate.relative_to(allowed)
                except ValueError as error:
                    raise DeliveryError(f"禁止提交工作区外的目标 carrier 文件: {raw_file}") from error
            source_file = (source_root / relative).resolve()
            if source_file == candidate:
                copied.append(relative.as_posix())
                continue
            if source_file.exists():
                if not source_file.is_file() or source_file.is_symlink():
                    raise DeliveryError(f"禁止交付非普通文件: {raw_file}")
                candidate.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, candidate)
            elif candidate.exists():
                candidate.unlink()
            copied.append(relative.as_posix())
        return copied

    @staticmethod
    def _copy_reports(
        source_root: Path,
        destination_root: Path,
        raw_paths: Any,
        *,
        carrier: str,
    ) -> list[str]:
        """仅复制目标 carrier 目录中的 final.json 或 Markdown 审计报告。"""
        if not isinstance(raw_paths, list):
            return []
        copied: list[str] = []
        expected_carrier = WorkspaceManager.safe_carrier_name(carrier)
        for raw_path in raw_paths:
            source = Path(str(raw_path))
            if not source.is_absolute():
                source = source_root / source
            source = source.resolve()
            try:
                relative = source.relative_to(source_root)
            except ValueError:
                _logger.warning("跳过工作区外的报告: %s", raw_path)
                continue
            parts = relative.parts
            if any(part.casefold() in {".ai-repair", "artifacts", ".git"} for part in parts):
                _logger.warning("跳过不安全路径中的报告: %s", raw_path)
                continue
            valid_markdown = (
                source.suffix == ".md"
                and len(parts) >= 2
                and parts[0] in {"gathers", "spiders"}
                and parts[1] == expected_carrier
            )
            valid_final_json = (
                source.suffix == ".json"
                and len(parts) == 3
                and parts[0] in {"gathers", "spiders"}
                and parts[1] == expected_carrier
                and parts[2] == "final.json"
            )
            if not source.is_file() or not (valid_markdown or valid_final_json):
                _logger.warning("跳过非法审计报告: %s", raw_path)
                continue
            destination = destination_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source != destination:
                shutil.copy2(source, destination)
            copied.append(relative.as_posix())
        return copied

    @classmethod
    def _copy_audit_records(
        cls,
        source_root: Path,
        destination_root: Path,
        state: Mapping[str, Any],
    ) -> list[str]:
        """失败或超轮次时复制完整公开 AI 记录，成功只保留终态报告。"""
        if state.get("delivery_status") not in {"REPAIR_FAILED", "REPAIR_ROUND_LIMIT"}:
            return []
        audit_root = source_root / settings.artifact_dir_name
        if not audit_root.is_dir():
            return []
        copied: list[str] = []
        for source in audit_root.rglob("*"):
            allowed = source.is_file() and not source.is_symlink()
            if not allowed or source.suffix not in cls._AUDIT_SUFFIXES:
                continue
            try:
                if source.stat().st_size > _MAX_AUDIT_BYTES:
                    _logger.warning("跳过过大的审计产物: %s", source)
                    continue
                content = source.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as error:
                _logger.warning("跳过无法读取的审计产物: %s (%s)", source, type(error).__name__)
                continue
            relative = source.relative_to(source_root)
            destination = destination_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=destination.parent,
                    prefix=f".{destination.name}.",
                    suffix=".redacted",
                    delete=False,
                ) as output:
                    output.write(redact_output(content))
                    temporary = Path(output.name)
                shutil.copyfile(temporary, destination)
            except OSError as error:
                _logger.warning("跳过无法写入的脱敏审计产物: %s (%s)", source, type(error).__name__)
                continue
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink()
                    except OSError:
                        pass
            copied.append(relative.as_posix())
        return copied

    @staticmethod
    def _commit_message(state: Mapping[str, Any]) -> str:
        """生成带评论的提交说明。

        人工在 Git 上只看 commit 就得判断这次改动值不值，所以正文带上三段：
        线上报的什么错（``error``）、修复 agent 的分析和结论（``repair``）、
        审查 agent 的结论（``review``）。空段落直接不出现，不留空壳。
        """
        carrier = str(state.get("carrier") or "unknown")
        status = str(state.get("delivery_status") or "REPAIR")
        lines = [f"bug-repair-tasks: {carrier} ({status})"]

        error = _one_line(state.get("error"))
        if error:
            lines += ["", f"error: {error}"]

        analysis = _one_line(state.get("repair_analysis"), limit=800)
        summary = _one_line(state.get("repair_summary"))
        if analysis or summary:
            lines += ["", "repair:"]
            if analysis:
                lines.append(f"  analysis: {analysis}")
            if summary:
                lines.append(f"  summary: {summary}")

        review = _one_line(state.get("review_summary"))
        if review:
            lines += ["", "review:", f"  summary: {review}"]
        return "\n".join(lines)
