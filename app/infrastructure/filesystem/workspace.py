"""AI 隔离、验证和修复分支工作区适配器。

三类工作区职责严格分离：AI 工作区不含 Git 且只能修改目标 carrier；
准生产回归工作区含 Git 但只运行验证命令；修复分支工作区只在最终交付时
接收 AI 工作区的目标 carrier 和报告。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from git import InvalidGitRepositoryError, NoSuchPathError, Repo


class WorkspaceError(RuntimeError):
    """工作区创建或文件复制失败。"""


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

    AI 隔离工作区：修复和验证在此完成，不含 .git，只读取目标 carrier。
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

    def __init__(self, config: WorkspaceConfig | None = None) -> None:
        """创建工作区管理器。"""
        self._config = config or WorkspaceConfig()
        self._root: Path | None = None

    def create_task_workspace(
        self,
        source_repo: str | Path,
        task_key: str,
        *,
        source_project_path: str | Path,
        baseline_dir: str | Path,
        carrier: str,
        framework: str,
    ) -> Path:
        """为一个任务复制不含 Git 和敏感文件的隔离工作区。"""
        source = self.project_root(source_repo)
        if source != Path(source_project_path).resolve():
            raise WorkspaceError("源项目路径与 prepare_source_snapshot 不一致")
        if framework not in {"gathers", "spiders"}:
            raise WorkspaceError(f"无效的快照框架: {framework}")
        target = self._target(task_key)
        try:
            self._copy_source(source, target)
            self.restore_carrier_snapshot(target, baseline_dir, carrier, framework)
            if (target / ".git").exists():
                raise WorkspaceError("AI 工作区禁止包含 .git")
            self._write_ignore(target)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
        return target

    def reset_task_workspace(
        self,
        workspace: str | Path,
        source_repo: str | Path,
        baseline_dir: str | Path,
        source_project_path: str | Path,
        carrier: str,
    ) -> None:
        """从干净源项目重建任务工作区，再恢复本次任务的 carrier 基线。"""
        root = Path(workspace).resolve()
        source = self.project_root(source_repo)
        expected = Path(source_project_path).resolve()
        if source != expected:
            raise WorkspaceError("重试时源项目路径与原始快照不一致")
        if not root.is_dir():
            raise WorkspaceError("重试工作区不存在")
        temporary = root.with_name(root.name + ".reset")
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        try:
            self._copy_source(source, temporary)
            baseline_root = Path(baseline_dir).resolve()
            carrier_dir = self.safe_carrier_name(carrier)
            framework = "gathers" if (baseline_root / "gathers" / carrier_dir).is_dir() else "spiders"
            if not (baseline_root / framework / carrier_dir).is_dir():
                raise WorkspaceError("重试时基线中没有可恢复的 carrier")
            # restore_carrier_snapshot 会一次恢复新旧两个框架，避免重复复制。
            self.restore_carrier_snapshot(temporary, baseline_dir, carrier, framework)
            self._write_ignore(temporary)
            try:
                self._force_remove(root)
            except OSError as error:
                raise WorkspaceError(f"任务工作区清理失败，可能有文件被占用: {error}") from error
            temporary.replace(root)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    @classmethod
    def restore_carrier_snapshot(
        cls,
        workspace: str | Path,
        baseline_dir: str | Path,
        carrier: str,
        framework: str,
    ) -> None:
        """恢复快照中存在的新旧框架 carrier，保证分析看到同一份基线。"""
        root = Path(workspace).resolve()
        baseline = Path(baseline_dir).resolve()
        if framework not in {"gathers", "spiders"}:
            raise WorkspaceError(f"无效的快照框架: {framework}")
        carrier_name = cls.safe_carrier_name(carrier)
        required = baseline / framework / carrier_name
        if not required.is_dir():
            raise WorkspaceError("快照中缺少已授权的目标 carrier")
        if framework == "gathers" and not (required / "main.py").is_file():
            raise WorkspaceError("gathers 快照缺少 main.py")

        restored = 0
        for candidate_framework in ("gathers", "spiders"):
            source = baseline / candidate_framework / carrier_name
            if not source.is_dir():
                continue
            destination = root / candidate_framework / carrier_name
            if not destination.resolve().is_relative_to(root):
                raise WorkspaceError("carrier 工作区路径越界")
            if destination.exists():
                shutil.rmtree(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination, symlinks=False)
            restored += 1
        if not restored:
            raise WorkspaceError("快照没有可恢复的 carrier 目录")

    def _write_ignore(self, workspace: Path) -> None:
        """限制模型默认可见目录。目标文件仍可按路径读取。"""
        (workspace / ".claudeignore").write_text(
            "\n".join(
                [
                    "*",
                    "!gathers/",
                    "!spiders/",
                    "!framework/",
                    "!common/",
                    "!gathers_base/",
                    "!gather_providers/",
                    "!docs/",
                    "",
                ]
            ),
            encoding="utf-8",
        )

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
            try:
                shutil.rmtree(target)
            except OSError:
                self._force_remove(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            Repo.clone_from(str(git_root), str(target), local=True)
        except Exception as error:
            shutil.rmtree(target, ignore_errors=True)
            raise WorkspaceError(f"回归工作区克隆失败: {error}") from error
        try:
            project = self.project_root(target)
            self.copy_files(ai_workspace, project, changed_files)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
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

    def allocate(self, group: str, name: str) -> Path:
        """在工作区根下分配一个空目录；同名旧目录先删除。"""
        if self._root is None:
            self._root = self._config.root_dir or Path(tempfile.mkdtemp(prefix="ai-repair-"))
        target = self._root / group / self._safe_name(name)
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        return target

    @classmethod
    def verify_manifest(
        cls,
        workspace: str | Path,
        manifest: dict[str, str],
    ) -> tuple[bool, list[str]]:
        """验证工作区指定文件与不可变基线 manifest 完全一致。"""
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
        for framework in ("gathers", "spiders"):
            base = root / framework
            if not base.is_dir():
                continue
            for path in base.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    actual.add(path.relative_to(root).as_posix())
        mismatches.extend(sorted(actual - set(expected)))
        return not mismatches, sorted(set(mismatches))

    def read_carrier_texts(
        self,
        workspace: str | Path,
        carrier: str,
        *,
        max_bytes: int = 512 * 1024,
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

    @staticmethod
    def _force_remove(path: Path) -> None:
        """删除可能带只读属性的目录，失败时保留原始异常。"""
        def onerror(function, failed_path, exc_info):
            try:
                os.chmod(failed_path, stat.S_IWRITE)
                function(failed_path)
            except OSError:
                raise exc_info[1]

        shutil.rmtree(path, onerror=onerror)

    @classmethod
    def _require_directory(source_repo: str | Path) -> Path:
        """确认源项目目录存在。"""
        source = Path(source_repo).resolve()
        if not source.is_dir():
            raise WorkspaceError(f"源项目不存在: {source}")
        return source

    def project_root(self, source_repo: str | Path) -> Path:
        """解析业务项目目录：优先落在配置的 ``project_subdir``，否则用源目录本身。

        交付器必须复用这里的解析结果。``AI_REPAIR_SOURCE_REPO`` 常常指向仓库
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
    def _carrier_path(cls, root: Path, carrier: str) -> Path:
        """按 gathers 优先、spiders 兜底解析目标 carrier 路径。"""
        safe = cls._safe_name(carrier)
        candidates = [root / "gathers" / safe, root / "spiders" / safe]
        existing = [path.resolve() for path in candidates if path.is_dir()]
        if not existing:
            raise WorkspaceError(f"工作区缺少目标 carrier: {carrier}")
        # 新框架优先；只有不存在 gathers 实现时才回退到 legacy spiders。
        selected = existing[0] if (root / "gathers" / safe).is_dir() else existing[-1]
        allowed_root = selected.parent.resolve()
        try:
            selected.relative_to(allowed_root)
        except ValueError as error:
            raise WorkspaceError(f"carrier 路径越界: {carrier}") from error
        return selected

    @classmethod
    def locate_carrier_roots(cls, workspace: str | Path, carrier: str) -> list[str]:
        """Return carrier-local new and legacy implementation directories."""
        root = Path(workspace).resolve()
        safe = cls._safe_name(carrier)
        return [
            path.relative_to(root).as_posix()
            for path in (root / "gathers" / safe, root / "spiders" / safe)
            if path.is_dir()
        ]

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
            or any(lowered == item or lowered.startswith(item) for item in cls._SENSITIVE_NAMES)
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
    def _safe_name(value: str) -> str:
        """将任务键转换成安全目录名。"""
        return "".join(char if char.isalnum() or char in "-_." else "-" for char in value)


