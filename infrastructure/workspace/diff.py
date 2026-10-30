"""文本差异计算。

修复 agent 只输出结论，"到底改了哪些行"由这里从工作区算出来：拿改动前的基线副本
与当前文件逐行比较，产出删除/新增行记录，供报告、门禁证据和人工审查使用。
"""

from __future__ import annotations

import difflib
import hashlib
from pathlib import Path
from typing import Any

from config.settings import settings

MAX_LINES_PER_FILE = 120
MAX_LINE_CHARS = 400
MAX_FILES = 20
MAX_FILE_BYTES = 2 * 1024 * 1024

_IGNORED_PARTS = (".ai-bug-tasks", ".evidence", ".git")


def text_patch(before: str, after: str, name: str) -> str:
    lines = difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                 fromfile="a/" + name, tofile="b/" + name)
    return "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                   for line in lines)


def complete_changes(workspace: str | Path, baseline: str | Path) -> dict[str, Any]:
    """冻结完整文本及真实行号 patch；二进制明确标记，不冒充空文件。"""
    files = []
    for name in source_changes(workspace, baseline):
        entry: dict[str, Any] = {"path": name, "available": True}
        for key, root in (("before", baseline), ("after", workspace)):
            base = Path(root).resolve()
            target = (base / name).resolve()
            if not target.is_relative_to(base):
                raise ValueError("差异文件越出源码范围")
            entry[key + "_exists"] = target.is_file()
            try:
                data = target.read_bytes() if target.is_file() else b""
                if b"\x00" in data:
                    raise UnicodeError("binary")
                entry[key] = data.decode("utf-8")
            except UnicodeError:
                entry[key] = None
                entry.update(available=False, reason="二进制或非 UTF-8 文件，无法展示文本差异")
        entry["status"] = "added" if not entry["before_exists"] else "deleted" if not entry["after_exists"] else "modified"
        if entry["available"]:
            entry["patch"] = text_patch(entry["before"], entry["after"], name)
        files.append(entry)
    return {"files": files, "available": True}


def source_manifest(root: str | Path) -> dict[str, str]:
    """读取完整源码身份，忽略平台产物与解释器缓存，拒绝链接。"""
    base = Path(root).resolve()
    if not base.is_dir():
        raise ValueError(f"源码目录不存在: {base}")
    ignored = {*_IGNORED_PARTS, "artifacts"}
    values: dict[str, str] = {}
    for path in sorted(base.rglob("*")):
        relative = path.relative_to(base)
        if relative.parts[0] in ignored or any(part in {"__pycache__", ".pytest_cache"} for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"源码中存在符号链接: {relative.as_posix()}")
        if path.is_file() and path.name not in {"final.json", "task_summary.md"}:
            values[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return values


def source_changes(root: str | Path, baseline: str | Path | dict[str, str]) -> list[str]:
    """以实际字节计算完整新增、修改、删除集合，不截断文件清单。"""
    before = baseline if isinstance(baseline, dict) else source_manifest(baseline)
    after = source_manifest(root)
    return sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))

# 门禁与审查会在同一份未变的工作区上反复读同一批文件（算哈希、算 diff、查敏感信息、
# 过 AST）。这里按 (路径, mtime, 大小) 缓存结果：文件一旦被改动，键就变，自动失效，
# 因此不会返回过期内容，同时把重复 I/O 压到一次。
_TEXT_CACHE: dict[tuple[str, int, int], str | None] = {}
_HASH_CACHE: dict[tuple[str, int, int], str] = {}
_CACHE_MAX = 512


def _cache_key(path: Path) -> tuple[str, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path), stat.st_mtime_ns, stat.st_size)


def read_text_cached(path: Path | None, max_bytes: int | None = MAX_FILE_BYTES) -> str | None:
    """按 (路径, mtime, 大小) 缓存的文本读取；不存在、二进制/解码失败返回 None。

    ``max_bytes=None`` 表示不限制大小（与门禁逐文件检查的旧行为一致）。
    """
    if path is None or not path.is_file():
        return None
    key = _cache_key(path)
    if key is None:
        return None
    if key in _TEXT_CACHE:
        return _TEXT_CACHE[key]
    if len(_TEXT_CACHE) >= _CACHE_MAX:
        _TEXT_CACHE.clear()
    try:
        too_big = max_bytes is not None and path.stat().st_size > max_bytes
        text = None if too_big else path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = None
    _TEXT_CACHE[key] = text
    return text


def sha256_cached(path: Path) -> str:
    """按 (路径, mtime, 大小) 缓存的 sha256。"""
    key = _cache_key(path)
    if key is not None and key in _HASH_CACHE:
        return _HASH_CACHE[key]
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    if key is not None:
        if len(_HASH_CACHE) >= _CACHE_MAX:
            _HASH_CACHE.clear()
        _HASH_CACHE[key] = value
    return value


def _clip(line: str) -> str:
    """单行过长时截断：压缩过的一整行代码贴进记录没有可读价值。"""
    return line if len(line) <= MAX_LINE_CHARS else f"{line[:MAX_LINE_CHARS]}…"


def diff_lines(before: str, after: str, max_lines: int = MAX_LINES_PER_FILE) -> dict[str, Any]:
    """返回增删行数与逐行差异（unified diff，不带上下文行）。"""
    added = 0
    removed = 0
    lines: list[dict[str, str]] = []
    for item in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0):
        if item.startswith("---") or item.startswith("+++"):
            continue
        if item.startswith("@@"):
            lines.append({"type": "hunk", "text": item})
            continue
        if item.startswith("+"):
            added += 1
            lines.append({"type": "add", "text": _clip(item[1:])})
        elif item.startswith("-"):
            removed += 1
            lines.append({"type": "del", "text": _clip(item[1:])})
    return {
        "added": added,
        "removed": removed,
        "truncated": len(lines) > max_lines,
        "lines": lines[:max_lines],
    }


def file_changes(
    baseline_dir: str | Path | None,
    root: str | Path,
    files: list[str],
    *,
    max_files: int = MAX_FILES,
) -> list[dict[str, Any]]:
    """逐文件比较基线副本与当前工作区，返回改动前 / 改动后的代码行。

    ``baseline_dir`` 与 ``root`` 使用同一套工作区相对路径，因此直接按相对路径对应。
    读取失败（二进制、超大、不存在）时只给出状态，不编造差异；**内容一致的文件不返回**
    （否则会出现"modified 但 0 增 0 删"这种自相矛盾的证据）。
    """
    root_path = Path(root)
    baseline_path = Path(baseline_dir) if baseline_dir else None
    changes: list[dict[str, Any]] = []
    for raw in list(files)[:max_files]:
        relative = str(raw).replace("\\", "/").lstrip("/")
        if not relative or relative.startswith(".."):
            continue
        current = root_path / relative
        baseline_file = baseline_path / relative if baseline_path is not None else None
        baseline_exists = baseline_file is not None and baseline_file.is_file()
        current_exists = current.is_file()
        before = _read_text(baseline_file)
        after = _read_text(current if current_exists else None)
        if (baseline_exists and before is None) or (current_exists and after is None):
            changes.append(
                {
                    "path": relative,
                    "status": "modified_too_large",
                    "added": 0,
                    "removed": 0,
                    "lines": [],
                    "too_large": True,
                }
            )
            continue
        if not baseline_exists and not current_exists:
            continue
        if not baseline_exists:
            status = "added"
        elif not current_exists:
            status = "deleted"
        elif before == after:
            continue  # 内容一致：没有改动，不要造出 modified 0/0 的假条目
        else:
            status = "modified"
        changes.append(
            {"path": relative, "status": status, **diff_lines(before or "", after or "")}
        )
    return changes


def changed_paths(
    root: str | Path,
    manifest: dict[str, str] | None,
    *,
    prefix: str = "",
    max_files: int = MAX_FILES,
) -> list[str]:
    """按内容哈希找出相对基线的真实变化文件（新增 / 修改 / 删除）。

    只按"模型声明了哪些文件"算 diff 是不够的：**未被声明的改动**（例如顺手给空的
    ``__init__.py`` 加一行注释）也必须暴露出来，否则"这一轮到底改了什么"根本没法核对。

    Args:
        root: 工作区根目录（与 ``manifest`` 的 key 同一基准）。
        manifest: 基线哈希清单（工作区相对路径 → sha256）。
        prefix: 只看这个子目录（carrier 目录），避免把无关目录算进来。
        max_files: 最多返回多少个文件，防止异常情况下清单无限膨胀。
    """
    if not manifest:
        return []
    root_path = Path(root)
    base = root_path / prefix if prefix else root_path
    current: dict[str, str] = {}
    for path in base.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root_path).as_posix()
        if any(part in _IGNORED_PARTS for part in Path(relative).parts):
            continue
        try:
            current[relative] = _sha256_file(path)
        except OSError:
            continue
    changed = sorted(
        key for key in set(current) | set(manifest) if current.get(key) != manifest.get(key)
    )
    return changed[:max_files]


def _sha256_file(path: Path) -> str:
    """文件内容哈希（带缓存）。"""
    return sha256_cached(path)


def _read_text(path: Path | None) -> str | None:
    """读取文本文件（带缓存）；不存在、二进制或超大文件返回 None。"""
    return read_text_cached(path)


__all__ = [
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "MAX_LINES_PER_FILE",
    "changed_paths",
    "diff_lines",
    "file_changes",
]
