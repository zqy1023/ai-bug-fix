"""AI 隔离、验证和修复分支工作区适配器。

三类工作区职责严格分离：AI 工作区不含 Git 且只能修改目标 carrier；
准生产回归工作区含 Git 但只运行验证命令；修复分支工作区只在最终交付时
接收 AI 工作区的目标 carrier 和报告。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from git import InvalidGitRepositoryError, NoSuchPathError, Repo

from config.settings import settings
from app.infrastructure.filesystem.locking import shared_cache_lock

# Windows 文件名非法字符；读目录名只替换这些，保留中文与可读性。
_INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class WorkspaceError(RuntimeError):
    """工作区创建或文件复制失败。"""


def _clone_repo(git_root: Path, target: Path, *, sparse: str = "") -> Repo:
    """克隆源仓库。优先硬链接，跨盘或权限不足时退回普通复制。

    ``local=True`` 在同盘能省一次对象复制，但 Windows 上硬链接失败会直接
    中断整个克隆（``failed to create link: Improper link``）。交付、共享源码树
    和回归工作区都走这里，不能让这一种文件系统限制变成所有任务的共同失败。

    ``sparse`` 给定时先以 ``--sparse`` 克隆、再把工作树收敛到该锥形目录，
    这样共享源码树不必先物化整仓库再删掉。回归和交付工作区需要完整树，
    不传该参数。
    """
    options = ["--sparse"] if sparse else []
    try:
        repo = Repo.clone_from(str(git_root), str(target), local=True, multi_options=options)
    except Exception as local_error:
        if target.exists():
            # 半成品克隆里可能已有只读的 .git/objects，裸 rmtree 会静默失败，
            # 下一次复用同一路径就撞 WinError 5。
            try:
                WorkspaceManager._force_remove(target)
            except OSError:
                pass
        try:
            repo = Repo.clone_from(str(git_root), str(target), multi_options=options)
        except Exception:
            raise local_error from None
    if sparse:
        # 非锥形模式：连仓库根目录文件也不检出，只留业务目录。
        repo.git.sparse_checkout("set", "--no-cone", f"/{sparse}/")
    return repo


@dataclass(frozen=True)
class WorkspaceConfig:
    """工作区目录和生命周期配置。"""

    # root_dir：隔离工作区和快照的根目录。
    root_dir: Path | None = None
    # project_subdir：源仓库内项目代码所在的相对目录。
    project_subdir: Path | None = None
    # keep：是否保留本轮工作区用于排查与审计。
    keep: bool = True


class WorkspaceManager:
    """创建并管理 AI 隔离工作区（不含 Git）与回归独立克隆。

    AI 隔离工作区：修复和验证在此完成，不含 .git。按提交复用过滤后的只读
    干净树，任务工作区只复制目标 carrier，共享依赖直接只读访问。
    回归独立克隆：从源仓库本地克隆，带 Git 历史，用后删除。
    交付工作区：由交付器临时克隆，在独立分支操作后推送，不碰源仓库检出。
    """

    _SENSITIVE_NAMES = (
        ".env",
        ".env.",
        ".pem",
        ".key",
        ".crt",
        "credentials",
        "secret",
        "secrets",
        "auth",
        "token",
        "password",
        "id_rsa",
    )
    _SENSITIVE_SUFFIXES = (".pem", ".key", ".crt")
    _BUILD_ARTIFACT_SUFFIXES = (".pyc", ".pyo", ".pyd")
    _SKIP_DIRS = {
        ".git",
        ".idea",
        ".venv",
        "__pycache__",
        "logs",
        "nacos-data",
        "node_modules",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
    }

    # 修改过滤行为时递增版本；名单内容本身也参与缓存键。
    _SHARED_FILTER_VERSION = 1
    _SHARED_SUFFIXES = {
        ".py", ".json", ".js", ".csv", ".txt", ".xml", ".html", ".htm", ".yaml", ".yml",
    }
    _SHARED_RELATIVE = {"docs/rules.md"}

    def __init__(self, config: WorkspaceConfig | None = None) -> None:
        """创建工作区管理器。"""
        self._config = config or WorkspaceConfig()
        self._root: Path | None = None

    def prepare_run_tree(self, source_repo: str | Path, run_id: str) -> Path:
        """按提交、业务路径及过滤规则复用只读基线，并固定本轮引用。"""
        source = self._require_directory(source_repo)
        if self._root is None:
            self._root = self._config.root_dir or Path(tempfile.mkdtemp(prefix="ai-repair-"))
        shared = self._root / "shared"
        with shared_cache_lock(shared):
            # 历史 checkpoint 继续使用原路径，不移动或重写旧任务的基线。
            legacy = shared / self._safe_name(run_id)
            if legacy.is_dir() and ((legacy / "gathers").is_dir() or (legacy / "spiders").is_dir()):
                return legacy.resolve()
            reference = shared / "refs" / f"{self._safe_name(run_id)}.json"
            rules = {
                "version": self._SHARED_FILTER_VERSION,
                "suffixes": sorted(self._SHARED_SUFFIXES),
                "relative": sorted(self._SHARED_RELATIVE),
                "skip_dirs": sorted(self._SKIP_DIRS),
                "sensitive_names": self._SENSITIVE_NAMES,
                "sensitive_suffixes": self._SENSITIVE_SUFFIXES,
                "build_suffixes": self._BUILD_ARTIFACT_SUFFIXES,
            }
            fingerprint = hashlib.sha256(json.dumps(rules, sort_keys=True).encode()).hexdigest()
            if reference.is_file():
                metadata = json.loads(reference.read_text(encoding="utf-8"))
                if not re.fullmatch(r"cache-[0-9a-f]{64}", str(metadata.get("cache") or "")):
                    raise WorkspaceError("共享源码引用损坏，拒绝改用其它版本")
                target = shared / metadata["cache"]
                if target.is_dir():
                    return target.resolve()
                if metadata.get("filter") != fingerprint:
                    raise WorkspaceError("旧过滤规则的共享基线已回收，请重新发起执行")
            else:
                try:
                    with Repo(source, search_parent_directories=True) as repo:
                        git_root = Path(repo.working_tree_dir or source).resolve()
                        commit = repo.head.commit.hexsha
                except (InvalidGitRepositoryError, NoSuchPathError, OSError, ValueError) as error:
                    raise WorkspaceError(f"源项目不是有效 Git 仓库: {source}") from error
                project = self.project_root(source).relative_to(git_root).as_posix()
                key = hashlib.sha256(json.dumps([commit, project, fingerprint]).encode()).hexdigest()
                metadata = {"cache": f"cache-{key}", "commit": commit, "project": project,
                            "filter": fingerprint, "run_id": run_id}
                target = shared / metadata["cache"]
            if not target.is_dir():
                self._publish_shared_tree(source, target, metadata)
            # 发布树和引用受同一跨进程锁保护，回收器不会看到无引用的在用缓存。
            reference.parent.mkdir(parents=True, exist_ok=True)
            temporary = reference.with_suffix(".tmp")
            temporary.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
            temporary.replace(reference)
            return target.resolve()

    def _publish_shared_tree(self, source: Path, target: Path, metadata: dict[str, str]) -> None:
        """从指定提交构建基线；只在完整过滤并设为只读后原子发布。"""
        with Repo(source, search_parent_directories=True) as source_git:
            git_root = Path(source_git.working_tree_dir or source).resolve()
        project = Path(metadata["project"])
        if project.is_absolute() or ".." in project.parts:
            raise WorkspaceError("共享源码业务路径越界")
        temporary = target.with_name(f"{target.name}.tmp-{uuid.uuid4().hex}")
        try:
            sparse = "" if project == Path(".") else project.as_posix()
            with _clone_repo(git_root, temporary, sparse=sparse) as repo:
                repo.git.checkout("--detach", metadata["commit"])
            cloned_project = temporary / project
            if not cloned_project.is_dir():
                raise WorkspaceError(f"提交中缺少业务目录: {project.as_posix()}")
            self._force_remove(temporary / ".git")
            self._sanitize_shared_tree(cloned_project)
            self._make_read_only(cloned_project)
            if project == Path("."):
                temporary.rename(target)
            else:
                cloned_project.rename(target)
        finally:
            if temporary.exists():
                self._force_remove(temporary)

    def create_task_workspace(
        self,
        shared_project: str | Path,
        task_key: str,
        *,
        baseline_dir: str | Path,
        carrier: str,
        framework: str,
    ) -> Path:
        """创建任务工作区：只放目标 carrier 的新旧两个目录。

        分析、修复和审查都只允许访问 carrier 自身；共享树不再镜像进任务目录，
        避免把整棵项目树和硬链接元数据复制给每个任务。目标 carrier 从不可变
        快照恢复为可写真实文件。
        """
        self._require_directory(shared_project)
        if framework not in {"gathers", "spiders"}:
            raise WorkspaceError(f"无效的快照框架: {framework}")
        target = self._target(task_key)
        try:
            self.restore_carrier_snapshot(target, baseline_dir, carrier, framework)
            if (target / ".git").exists():
                raise WorkspaceError("AI 工作区禁止包含 .git")
        except Exception:
            if target.exists():
                self._force_remove(target)
            raise
        return target

    def reset_task_workspace(
        self,
        workspace: str | Path,
        baseline_dir: str | Path,
        carrier: str,
    ) -> None:
        """重试前只恢复本任务 carrier；共享只读目录仍是本轮唯一副本。"""
        root = Path(workspace).resolve()
        if not root.is_dir():
            raise WorkspaceError("重试工作区不存在")
        baseline_root = Path(baseline_dir).resolve()
        framework = (
            "gathers"
            if type(self).find_carrier_dir(baseline_root, "gathers", carrier) is not None
            else "spiders"
        )
        if type(self).find_carrier_dir(baseline_root, framework, carrier) is None:
            raise WorkspaceError("重试时基线中没有可恢复的 carrier")
        # restore_carrier_snapshot 会一次恢复新旧两个框架，避免重复复制。
        self.restore_carrier_snapshot(root, baseline_dir, carrier, framework)

    @classmethod
    def restore_carrier_snapshot(
        cls,
        workspace: str | Path,
        baseline_dir: str | Path,
        carrier: str,
        framework: str,
    ) -> None:
        """恢复快照中存在的新旧框架 carrier，保证分析看到同一份基线。

        目录名必须沿用快照里的真实大小写：源码常见 ``gathers/Airpak``，而
        ``safe_carrier_name`` 会 casefold。Linux 上按小写拼接会找不到目录，
        Windows 上 ``git status``/manifest 的路径大小写也会与真实文件不一致。
        """
        root = Path(workspace).resolve()
        baseline = Path(baseline_dir).resolve()
        if framework not in {"gathers", "spiders"}:
            raise WorkspaceError(f"无效的快照框架: {framework}")
        required = cls.find_carrier_dir(baseline, framework, carrier)
        if required is None:
            raise WorkspaceError("快照中缺少已授权的目标 carrier")
        if framework == "gathers" and not (required / "main.py").is_file():
            raise WorkspaceError("gathers 快照缺少 main.py")

        restored = 0
        for candidate_framework in ("gathers", "spiders"):
            source = cls.find_carrier_dir(baseline, candidate_framework, carrier)
            if source is None:
                continue
            destination = root / candidate_framework / source.name
            if not destination.resolve().is_relative_to(root):
                raise WorkspaceError("carrier 工作区路径越界")
            if destination.exists():
                cls._force_remove(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination, symlinks=False)
            restored += 1
        if not restored:
            raise WorkspaceError("快照没有可恢复的 carrier 目录")

    @classmethod
    def _sanitize_shared_tree(cls, root: Path) -> None:
        """保留源码、配置、规则文档和 carrier 运行资产，剔除缓存/日志/凭据。

        只留 ``.py``/``.json`` 会让定位/修复 Agent 看不到 ``docs/rules.md``、
        carrier 自带的 ``.js``/``.csv``/``.txt`` 等真实运行资产，也会让 response
        重放缺少框架外文件。这里按后缀白名单保留这些文件，凭据文件仍由
        ``_is_sensitive`` 排除。
        """
        kept_suffixes = cls._SHARED_SUFFIXES
        kept_relative = cls._SHARED_RELATIVE
        for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            kept: list[str] = []
            for name in directories:
                candidate = current_path / name
                if not (
                    candidate.is_symlink()
                    or cls._is_skipped_dir(name)
                    or cls._is_sensitive(name)
                ):
                    kept.append(name)
                    continue
                if candidate.is_dir() and not candidate.is_symlink():
                    cls._force_remove(candidate)
                else:
                    candidate.unlink()
            directories[:] = kept
            for name in filenames:
                candidate = current_path / name
                relative = candidate.relative_to(root).as_posix().casefold()
                if (
                    candidate.is_symlink()
                    or cls._is_sensitive(name)
                    or cls._is_skipped_dir(name)
                    or (
                        candidate.suffix.lower() not in kept_suffixes
                        and relative not in kept_relative
                    )
                ):
                    candidate.unlink()
        # 文件删完后目录可能为空，逐层回收；根目录保留。
        for current, _directories, _filenames in os.walk(root, topdown=False):
            path = Path(current)
            if path != root and not any(path.iterdir()):
                path.rmdir()

    @classmethod
    def _make_read_only(cls, root: Path) -> None:
        """把共享源码树中的普通文件设为只读，防止任务通过硬链接改写共享内容。"""
        for current, _directories, filenames in os.walk(root):
            for name in filenames:
                path = Path(current) / name
                if path.is_symlink():
                    continue
                try:
                    os.chmod(path, stat.S_IREAD)
                except OSError as error:
                    raise WorkspaceError(f"共享源码文件设置只读失败: {path}") from error

    def copy_carrier(
        self,
        ai_workspace: str | Path,
        destination_workspace: str | Path,
        carrier: str,
    ) -> list[str]:
        """把 AI 工作区中的目标 carrier 复制到回归或分支工作区。"""
        source_root = Path(ai_workspace).resolve()
        destination_root = Path(destination_workspace).resolve()
        source = self._carrier_path(source_root, carrier)
        destination = self._carrier_path(destination_root, carrier)
        if not source.is_dir():
            raise WorkspaceError(f"AI 工作区缺少目标 carrier: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination, symlinks=False)
        return [
            path.relative_to(destination_root).as_posix()
            for path in destination.rglob("*")
            if path.is_file()
        ]

    def create_regression_workspace(
        self,
        source_repo: str | Path,
        task_key: str,
        ai_workspace: str | Path,
        changed_files: list[str],
    ) -> Path:
        """从源仓库克隆一个只用于回归的 Git 工作区，再叠上 AI 隔离区的改动。

        克隆自带源仓库的历史，因此回归命令若顺手提交，HEAD 会前进，调用方据此判失败。
        该目录不复用开发人员正在使用的源仓库，失败时整体删除。
        """
        source = self._require_directory(source_repo)
        try:
            git_root = Path(Repo(source, search_parent_directories=True).working_tree_dir or source).resolve()
        except (InvalidGitRepositoryError, NoSuchPathError, OSError, ValueError) as error:
            raise WorkspaceError(f"源项目不是本地 Git 仓库，无法建立回归环境: {source}") from error
        if self._root is None:
            self._root = self._config.root_dir or Path(tempfile.mkdtemp(prefix="ai-repair-"))
        target = self._root / "regression" / self._safe_name(task_key)
        if target.exists():
            # 回归工作区一次性使用，进程崩溃留下的陈旧残留在本次执行前自愈。
            # _force_remove 会把环境删除守卫的 SystemExit 转成 WorkspaceError，
            # 由步骤失败处理，而不是杀死工作流线程。
            self._cleanup_path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            _clone_repo(git_root, target)
        except Exception as error:
            self._cleanup_path(target)
            raise WorkspaceError(f"回归工作区克隆失败: {error}") from error
        try:
            project = self.project_root(target)
            self.copy_files(ai_workspace, project, changed_files)
        except Exception:
            self._cleanup_path(target)
            raise
        return target

    def copy_files(
        self,
        ai_workspace: str | Path,
        destination_workspace: str | Path,
        relative_files: list[str],
    ) -> list[str]:
        """按工作区相对路径把指定文件从 AI 隔离区覆盖到目标工作区，支持删除。"""
        source_root = Path(ai_workspace).resolve()
        destination_root = Path(destination_workspace).resolve()
        copied: list[str] = []
        for raw in relative_files:
            relative = Path(str(raw).replace("\\", "/"))
            if not relative.parts or relative.is_absolute() or ".." in relative.parts:
                raise WorkspaceError(f"拒绝复制工作区外的文件: {raw}")
            source = (source_root / relative).resolve()
            destination = (destination_root / relative).resolve()
            try:
                source.relative_to(source_root)
                destination.relative_to(destination_root)
            except ValueError as error:
                raise WorkspaceError(f"拒绝复制工作区外的文件: {raw}") from error
            if source.exists():
                if not source.is_file() or source.is_symlink():
                    raise WorkspaceError(f"拒绝复制非普通文件: {raw}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
            elif destination.exists():
                if destination.is_dir():
                    shutil.rmtree(destination)
                else:
                    destination.unlink()
            copied.append(relative.as_posix())
        return copied

    def allocate(self, group: str, name: str, *, readable: bool = False) -> Path:
        """在工作区根下分配一个空目录；同名旧目录先删除。

        旧目录可能是带 Git 元数据的克隆（``.git/objects`` 为硬链接且只读），
        Windows 上裸 ``shutil.rmtree`` 会报 ``WinError 5``。必须走带 chmod
        重试的 ``_force_remove``，否则一次残留就让后续交付全部失败。

        ``readable=True`` 时只替换文件系统非法字符，保留分支名可读性；默认的
        ``_safe_name`` 会把 ``/`` 和中文编码成 ``%XX``，只适合内部任务键。
        """
        if self._root is None:
            self._root = self._config.root_dir or Path(tempfile.mkdtemp(prefix="ai-repair-"))
        segment = self._readable_name(name) if readable else self._safe_name(name)
        target = self._root / group / segment
        if target.exists():
            self._force_remove(target)
        target.mkdir(parents=True, exist_ok=True)
        return target

    @classmethod
    def verify_manifest(
        cls,
        workspace: str | Path,
        manifest: dict[str, str],
        *,
        carrier: str | None = None,
    ) -> tuple[bool, list[str]]:
        """验证工作区指定文件与不可变基线 manifest 完全一致。

        ``carrier`` 给定时只扫描该 carrier 的新旧框架目录，避免把共享树里
        其他 carrier 的只读文件误判成本任务的额外改动。
        """
        root = Path(workspace).resolve()
        mismatches: list[str] = []
        expected = {str(key).replace("\\", "/"): str(value) for key, value in manifest.items()}
        for relative, digest in expected.items():
            target = (root / relative).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                mismatches.append(relative)
                continue
            if not target.is_file() or cls._sha256(target) != digest:
                mismatches.append(relative)
        actual: set[str] = set()
        scopes: list[Path] = []
        if carrier:
            # 按磁盘上的真实大小写定位，不能用 casefold 后的名字拼路径。
            for framework in ("gathers", "spiders"):
                found = cls.find_carrier_dir(root, framework, carrier)
                if found is not None:
                    scopes.append(found)
        else:
            scopes = [root / "gathers", root / "spiders"]
        for base in scopes:
            if not base.is_dir():
                continue
            for path in base.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    actual.add(path.relative_to(root).as_posix())
        # Windows 的 manifest 可能来自大小写不同的旧记录；比较用 normcase，
        # 上报仍保留真实路径。Linux 上 normcase 是恒等，大小写不一致仍会被拒绝。
        expected_by_norm = {os.path.normcase(path): path for path in expected}
        actual_by_norm = {os.path.normcase(path): path for path in actual}
        mismatches.extend(
            actual_by_norm[key]
            for key in sorted(set(actual_by_norm) - set(expected_by_norm))
        )
        return not mismatches, sorted(set(mismatches))

    def read_carrier_texts(
        self,
        workspace: str | Path,
        carrier: str,
        *,
        max_bytes: int = settings.workspace_read_max_bytes,
    ) -> dict[str, str]:
        """读取目标 carrier 的可读文本（工作区相对路径 → 文本内容）。

        只按哈希记基线无法还原"改了哪几行"，因此修改前额外保留一份内容副本；
        二进制或超大文件跳过（差异计算只对文本有意义）。
        """
        root = Path(workspace).resolve()
        carrier_root = self._carrier_path(root, carrier)
        texts: dict[str, str] = {}
        for path in carrier_root.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                if path.stat().st_size > max_bytes:
                    continue
                texts[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        return texts

    @classmethod
    def resolve_carrier_path(cls, workspace: str | Path, carrier: str) -> Path:
        """解析工作区内唯一的 carrier 目录，供各适配器共享路径规则。"""
        return cls._carrier_path(Path(workspace).resolve(), carrier)

    def cleanup(self, workspace: str | Path) -> None:
        """按配置清理任务工作区。"""
        if not self._config.keep:
            shutil.rmtree(Path(workspace), ignore_errors=True)

    def _target(self, task_key: str) -> Path:
        """生成唯一任务目录；已有目录不得被重试覆盖。"""
        if self._root is None:
            self._root = self._config.root_dir or Path(tempfile.mkdtemp(prefix="ai-repair-"))
        target = self._root / "ai" / self._safe_name(task_key)
        if target.exists():
            raise WorkspaceError(f"任务工作区已存在，拒绝覆盖: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def _cleanup_path(self, path: Path) -> None:
        """清理半成品工作区；删除失败不能掩盖调用方的原始异常。"""
        if not path.exists():
            return
        try:
            self._force_remove(path)
        except (OSError, WorkspaceError):
            pass

    @staticmethod
    def _force_remove(path: Path) -> None:
        """删除可能带只读属性的目录，失败时保留原始异常。"""
        def onerror(function, failed_path, exc_info):
            try:
                os.chmod(failed_path, stat.S_IWRITE)
                function(failed_path)
            except OSError:
                raise exc_info[1]

        try:
            shutil.rmtree(path, onerror=onerror)
        except SystemExit as error:
            # 环境级删除守卫会抛 SystemExit（BaseException）；转成工作区错误，
            # 让上层步骤按失败处理，而不是直接杀死工作流线程。
            raise WorkspaceError(f"删除工作区被环境拦截: {path}") from error

    @staticmethod
    def _require_directory(source_repo: str | Path) -> Path:
        """确认源项目目录存在。"""
        source = Path(source_repo).resolve()
        if not source.is_dir():
            raise WorkspaceError(f"源项目不存在: {source}")
        return source

    def project_root(self, source_repo: str | Path) -> Path:
        """解析业务项目目录：优先落在配置的 ``project_subdir``，否则用源目录本身。

        交付器必须复用这里的解析结果。本地模式下 ``AI_REPAIR_SOURCE_REPO`` 常指向仓库
        根，而``gathers/``、``spiders/`` 实际位于 ``project_subdir`` 之下；
        直接拿仓库根当业务目录会报"工作区缺少目标 carrier"。
        """
        source = self._require_directory(source_repo)
        configured = self._config.project_subdir
        if configured is None:
            return source
        if configured.is_absolute():
            raise WorkspaceError("project_subdir 必须是相对 Git 根目录的路径")
        try:
            git_root = Path(Repo(source).working_tree_dir or source).resolve()
        except (InvalidGitRepositoryError, NoSuchPathError, OSError, ValueError) as error:
            raise WorkspaceError(f"源项目不是本地 Git 仓库，无法解析 project_subdir: {source}") from error
        project = (git_root / configured).resolve()
        try:
            project.relative_to(git_root)
        except ValueError as error:
            raise WorkspaceError(f"业务目录不在 Git 根目录内: {project}") from error
        if not project.is_dir():
            raise WorkspaceError(f"业务项目目录不存在: {project}")
        return project

    @classmethod
    def _copy_source(cls, source: Path, target: Path) -> None:
        """复制源码并确定性排除 Git、缓存和敏感文件。

        Windows 上逐文件 copy2 会撞上实时杀毒扫描，几千个文件要几分钟；
        这里先收集全部拷贝任务再用线程池并行写，首次创建耗时能降一个量级。
        """
        jobs: list[tuple[Path, Path]] = []
        for current, directories, filenames in os.walk(source, followlinks=False):
            current_path = Path(current)
            relative = current_path.relative_to(source)
            if any(cls._is_skipped_dir(part) for part in relative.parts):
                directories[:] = []
                continue
            directories[:] = [
                name
                for name in directories
                if not cls._is_skipped_dir(name) and not cls._is_sensitive(name)
            ]
            destination_dir = target / relative
            destination_dir.mkdir(parents=True, exist_ok=True)
            for filename in filenames:
                source_file = current_path / filename
                if source_file.is_symlink() or cls._is_sensitive(filename):
                    continue
                jobs.append((source_file, destination_dir / filename))
        if not jobs:
            return
        workers = min(16, max(4, (os.cpu_count() or 4) * 2))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(cls._copy_file, jobs))

    @staticmethod
    def _copy_file(job: tuple[Path, Path]) -> None:
        """复制单个文件；只保内容不保元数据，diff 与哈希都以内容为准。"""
        source_file, destination = job
        shutil.copyfile(source_file, destination)

    @classmethod
    def find_child_dir(cls, root: str | Path, name: str) -> Path | None:
        """按大小写无关的名字查找直接子目录，返回磁盘上的真实路径。

        源码目录普遍是 ``gathers/Airpak``，而任务键和配置常写成小写。Linux 上
        必须使用真实目录名；Windows 上也要保留真实大小写，才能让 Git/manifest
        的路径比较稳定。
        """
        base = Path(root).resolve()
        if not base.is_dir():
            return None
        wanted = str(name).casefold()
        matches = [
            path
            for path in base.iterdir()
            if path.is_dir() and not path.is_symlink() and path.name.casefold() == wanted
        ]
        if not matches:
            return None
        if len(matches) > 1:
            raise WorkspaceError(f"目录大小写冲突: {base / name}")
        return matches[0].resolve()

    @classmethod
    def find_carrier_dir(
        cls, root: str | Path, framework: str, carrier: str
    ) -> Path | None:
        """按框架查找真实 carrier 目录；不存在返回 ``None``。

        ``carrier`` 先经过与工作区布局相同的安全编码，再按大小写无关匹配。
        """
        return cls.find_child_dir(Path(root) / framework, cls.safe_carrier_name(carrier))

    @classmethod
    def _carrier_path(cls, root: Path, carrier: str) -> Path:
        """按 gathers 优先、spiders 兜底解析目标 carrier 路径。"""
        gathers = cls.find_carrier_dir(root, "gathers", carrier)
        legacy = cls.find_carrier_dir(root, "spiders", carrier)
        selected = gathers or legacy
        if selected is None:
            raise WorkspaceError(f"工作区缺少目标 carrier: {carrier}")
        try:
            selected.relative_to(root)
        except ValueError as error:
            raise WorkspaceError(f"carrier 路径越界: {carrier}") from error
        return selected

    @classmethod
    def locate_carrier_roots(cls, workspace: str | Path, carrier: str) -> list[str]:
        """Return carrier-local new and legacy implementation directories."""
        root = Path(workspace).resolve()
        paths = [
            found
            for found in (
                cls.find_carrier_dir(root, "gathers", carrier),
                cls.find_carrier_dir(root, "spiders", carrier),
            )
            if found is not None
        ]
        return [path.relative_to(root).as_posix() for path in paths]

    # 提示词明确要求 agent 先读的官方规范。放进只读白名单的**逐个文件**，
    # 而不是整个 docs/：规范要读得到，其它项目文档不必暴露。
    RULE_FILES = (
        "docs/rules.md",
        "docs/项目梳理_架构与编码习惯_2026-09-20.md",
    )

    @classmethod
    def readable_rule_paths(cls, workspace: str | Path) -> list[str]:
        """返回工作区里实际存在的官方规范文件（工作区相对路径）。"""
        root = Path(workspace).resolve()
        return [item for item in cls.RULE_FILES if (root / item).is_file()]

    @classmethod
    def _is_skipped_dir(cls, name: str) -> bool:
        """大小写无关地排除 Git、缓存、日志和运行时数据目录。"""
        lowered = name.casefold()
        return any(lowered == item.casefold() for item in cls._SKIP_DIRS)

    @classmethod
    def _is_sensitive(cls, name: str) -> bool:
        """判断文件名属于凭据/敏感配置、认证/供应商 CSV 或编译产物。"""
        lowered = name.lower()
        stem = lowered[:-4] if lowered.endswith(".csv") else ""
        sensitive_csv = bool(stem) and any(
            marker in stem
            for marker in ("provider", "auth", "token", "credential", "secret", "password")
        )
        return (
            sensitive_csv
            or lowered.endswith(cls._SENSITIVE_SUFFIXES)
            or lowered.endswith(cls._BUILD_ARTIFACT_SUFFIXES)
            or any(
                (
                    lowered.startswith(item)
                    if item.endswith(".")
                    else lowered == item
                    if item.startswith(".")
                    else (
                        lowered == item
                        or lowered.startswith(item + "_")
                        or lowered.startswith(item + "-")
                        or lowered.startswith(item + ".")
                    )
                )
                for item in cls._SENSITIVE_NAMES
                if not item.startswith(".") or item in {".env", ".env."}
            )
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        """计算文件内容哈希。"""
        digest = hashlib.sha256()
        with path.open("rb") as file_obj:
            for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def safe_carrier_name(value: str) -> str:
        """carrier 键转安全目录名。工作区布局用它拼路径，外部也要按同一规则拼。"""
        return WorkspaceManager._safe_name(value)

    @staticmethod
    def _readable_name(value: str) -> str:
        """只替换文件系统非法字符，保留分支名/中文可读性。"""
        result = _INVALID_PATH_CHARS.sub("-", str(value)).strip(" .-")
        return result or "item"

    @staticmethod
    def _safe_name(value: str) -> str:
        """将任务键转换成安全目录名，避免非法字符和大小写造成碰撞。"""
        return "".join(
            char if char.isalnum() or char in "-_" or char == "." else f"%{ord(char):02X}"
            for char in str(value).casefold()
        )


