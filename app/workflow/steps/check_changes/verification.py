"""确定性范围、路径、AST 和敏感信息门禁。

除了"通过 / 不通过"，这里必须给出**可核对的证据**：做了哪些检查、每个文件各自
的结论（是否在目标 carrier 内、真实差异多少行、是否命中敏感信息、AST 是否通过），
以及模型声明的 ``changed_files`` 与真实工作区差异的对照。只有原因、没有证据的
门禁无法排查，也无法把"为什么被驳回"带回给修复 agent。
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Any

from app.infrastructure.filesystem.workspace import WorkspaceError, WorkspaceManager
from app.infrastructure.filesystem.diff import file_changes, read_text_cached, sha256_cached

# 只把"给敏感名赋了**字面值**"当成命中：
#   token = "a1b2c3d4e5f6"        命中（引号字面量，长度像凭据）
#   token = csrf_token_list[0]     不命中（表达式/变量，是普通代码）
#   password = os.environ["X"]     不命中
_SECRET = re.compile(
    r"(?is)(?:authorization|cookie|token|password|passwd|secret|api[_-]?key|bearer"
    r"|private[_-]?key)\s*[:=]\s*"
    r"(?:\"[^\"\n]{8,}\"|'[^'\n]{8,}'|[A-Za-z0-9+/_\-]{16,})"
)
from config.settings import settings

_IGNORED_PARTS = (settings.artifact_dir_name, ".evidence", ".git")
_DANGEROUS_CALLS = {"eval", "exec", "compile", "__import__"}
_DANGEROUS_ATTRIBUTES = {"popen", "Popen", "check_call", "check_output"}
_CHECKS = (
    "carrier 范围",
    "路径与符号链接",
    "硬链接",
    "敏感信息",
    "Python AST 危险调用",
    "声明与真实差异一致性",
)


class _DangerousNodeVisitor(ast.NodeVisitor):
    """检查新增 Python 代码中的高风险调用。"""

    def __init__(self) -> None:
        """初始化失败清单。"""
        self.failures: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        """拒绝动态执行和危险系统调用。"""
        if isinstance(node.func, ast.Name) and node.func.id in _DANGEROUS_CALLS:
            self.failures.append(f"禁止调用 {node.func.id}")
        if isinstance(node.func, ast.Attribute) and node.func.attr in _DANGEROUS_ATTRIBUTES:
            self.failures.append(f"禁止调用 {node.func.attr}")
        self.generic_visit(node)


def _under(root: Path, candidate: Path) -> bool:
    """判断候选路径是否位于允许目录。"""
    try:
        normalized_root = os.path.normcase(os.path.normpath(str(root.resolve())))
        normalized_candidate = os.path.normcase(os.path.normpath(str(candidate.resolve(strict=False))))
        Path(normalized_candidate).relative_to(Path(normalized_root))
        return True
    except ValueError:
        return False


def _sha256(path: Path) -> str:
    """计算文件内容哈希（带缓存，避免同一文件在门禁内被反复读取）。"""
    return sha256_cached(path)


def _actual_changed_files(
    root: Path, baseline: dict[str, str], scope: Path | None = None
) -> set[str]:
    """根据基线哈希计算真实修改、新增和删除文件。

    **只看基线覆盖的范围**（目标 carrier）：工作区有几千个文件，而基线只有 carrier 的
    那几个；把"基线里没有的文件"也算成变化，等于宣称每个文件都被改过——这正是过去
    "未声明的空文件差异"这类假门禁结论的来源，会把修复 agent 引去改无关文件。

    排除项按**相对路径**判断：工作区本身就在 ``.ai-repair/`` 下，按绝对路径判断会把
    所有文件都排除掉，函数就退化成"基线里每个文件都算变化"。
    """
    prefix = f"{scope.as_posix().rstrip('/')}/" if scope is not None else ""
    scoped = {
        key: value for key, value in baseline.items() if not prefix or key.startswith(prefix)
    }
    if not scoped:
        return set()
    base = root / scope if scope is not None else root
    current: dict[str, str] = {}
    for path in base.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if any(part in _IGNORED_PARTS for part in Path(relative).parts):
            continue
        current[relative] = _sha256(path)
    return {
        path for path in set(current) | set(scoped) if current.get(path) != scoped.get(path)
    }


def _secret_samples(text: str, stats: dict[str, Any]) -> list[dict[str, Any]]:
    """只在本轮**新增行**里找疑似敏感信息，并给出行号与命中片段。

    扫整份文件会把历史代码里的 ``token = csrf_token_list[0]`` 判成凭据：该行不是本轮
    改动，判它只会让门禁结论无法解释。新增行不可得（新文件整份都是新增、或 diff 被
    截断）时才退回整份文件，并在证据里标注来源。
    """
    added = {
        str(line.get("text", "")).strip()
        for line in (stats.get("lines") or [])
        if line.get("type") == "add"
    }
    whole_file = stats.get("status") == "added" or bool(stats.get("truncated"))
    samples: list[dict[str, Any]] = []
    for number, raw in enumerate(text.splitlines(), 1):
        if not whole_file and raw.strip() not in added:
            continue
        match = _SECRET.search(raw)
        if match:
            snippet = match.group(0).strip()
            samples.append(
                {
                    "line": number,
                    "snippet": snippet if len(snippet) <= 120 else f"{snippet[:119]}…",
                    "source": "whole-file" if whole_file else "added-line",
                }
            )
    return samples


def validate_changed_files(
    workspace: str | Path,
    carrier: str,
    changed_files: list[str],
    baseline_manifest: dict[str, str] | None = None,
    baseline_dir: str | Path | None = None,
    authorized_files: list[str] | None = None,
) -> dict[str, Any]:
    """校验真实 diff 只能修改目标 carrier，并执行 AST 与安全门禁。

    Args:
        workspace: 隔离工作区根目录。
        carrier: 本轮唯一允许修改的运输商标识。
        changed_files: 模型声明的修改清单（工作区相对路径）。
        baseline_manifest: 修改前的文件哈希（用于判定真实差异）。
        baseline_dir: 修改前的内容副本目录（用于给出逐文件增删行数）。
        authorized_files: 人工已批准的具体文件（工作区相对路径）。默认为空，
            此时只允许 carrier 内改动。授权只对方案里列出的这几个文件生效，
            不会因为批准了一个方案就放开同目录下的其它文件。
    """
    root = Path(workspace).resolve()
    claimed = {str(path).replace("\\", "/") for path in changed_files if str(path)}
    granted = {
        str(path).replace("\\", "/")
        for path in (authorized_files or [])
        if str(path) and _under(root, (root / str(path)).resolve(strict=False))
    }
    try:
        carrier_paths = [root / item for item in WorkspaceManager.locate_carrier_roots(root, carrier)]
    except WorkspaceError as error:
        return _blocked(str(error), carrier)
    if not carrier_paths and not granted:
        return _blocked(f"工作区缺少目标 carrier: {carrier}", carrier)
    allowed_root = [item.relative_to(root).as_posix() for item in carrier_paths]

    actual: set[str] = set()
    if baseline_manifest is not None:
        actual = set()
        for scope in allowed_root:
            actual.update(_actual_changed_files(root, baseline_manifest, Path(scope)))

    diff = {
        item["path"]: item
        for item in file_changes(baseline_dir, root, sorted(claimed | actual))
    }

    files: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    # 声明与差异不一致：逐文件给出结论，不用一句"不一致"糊过去
    compare_claims = bool(baseline_manifest)
    if compare_claims:
        for raw in sorted(actual - claimed):
            failures.append(
                {"file": raw, "reason": "真实变化但未声明", "code": "UNDECLARED_CHANGE"}
            )
        for raw in sorted(claimed - actual):
            failures.append(
                {"file": raw, "reason": "声明了但没有真实变化", "code": "PHANTOM_CLAIM"}
            )
    for raw in sorted(claimed | actual):
        entry = _inspect_file(root, carrier_paths, granted, raw, baseline_manifest, diff)
        files.append(entry)
        failures.extend(
            {"file": raw, "reason": reason, "code": "FILE_PROBLEM"}
            for reason in entry["problems"]
        )

    mismatch = {
        "missing_in_claim": sorted(actual - claimed),
        "extra_in_claim": sorted(claimed - actual),
    }
    return {
        "passed": not failures,
        "reason": _summary(failures),
        "failures": failures,
        "checks": list(_CHECKS),
        "scope": {
            "carrier": carrier,
            "allowed_root": allowed_root,
            "authorized_files": sorted(granted),
            "baseline_files": len(baseline_manifest or {}),
            "claimed_files": len(claimed),
            "actual_files": len(actual),
            "diffable": baseline_dir is not None,
        },
        "claim": {
            "claimed": sorted(claimed),
            "actual": sorted(actual),
            **mismatch,
            "consistent": not compare_claims or actual == claimed,
        },
        "totals": {
            "files": len(files),
            "added": sum(int(item.get("added") or 0) for item in files),
            "removed": sum(int(item.get("removed") or 0) for item in files),
        },
        "files": files,
    }


def _inspect_file(
    root: Path,
    allowed: list[Path],
    granted: set[str],
    raw: str,
    baseline_manifest: dict[str, str] | None,
    diff: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """逐文件检查并返回证据行；problems 同时作为失败原因使用。

    范围判定有两层：目标 carrier 目录，或人工逐文件批准的白名单。
    """
    candidate = (root / raw).resolve(strict=False)
    stats = diff.get(raw, {})
    in_carrier = any(_under(item, candidate) for item in allowed)
    entry: dict[str, Any] = {
        "path": raw,
        "in_scope": in_carrier or raw in granted,
        "exists": candidate.exists(),
        # 没有 diff 条目 = 内容与基线一致（例如被声明但其实没改）
        "status": str(stats.get("status", "unchanged")),
        "added": int(stats.get("added") or 0),
        "removed": int(stats.get("removed") or 0),
        # 逐行差异原文：没有它，记录里只有"+12 -3"这类数字，看不出改了什么
        "lines": list(stats.get("lines") or []),
        "truncated": bool(stats.get("truncated")),
        "size": 0,
        "sha256": "",
        "nlink": 0,
        "secret_hits": 0,
        "secret_samples": [],
        "ast": "skipped",
        "dangerous_calls": [],
        "problems": [],
    }
    problems: list[str] = entry["problems"]
    if not entry["in_scope"]:
        problems.append("文件既不在目标 carrier 范围内，也不在人工批准清单内")
        return entry
    if not candidate.exists():
        if baseline_manifest is not None and raw in baseline_manifest:
            # 删除只能作用于目标 carrier 中已存在的基线文件
            entry["status"] = "deleted"
            return entry
        problems.append("文件不存在或为符号链接")
        return entry
    if candidate.is_symlink() or not candidate.is_file():
        problems.append("文件不存在或为符号链接")
        return entry
    try:
        stat = candidate.stat()
        entry["size"] = stat.st_size
        entry["nlink"] = stat.st_nlink
        entry["sha256"] = _sha256(candidate)[:16]
        if stat.st_nlink > 1:
            problems.append("禁止提交硬链接文件")
        text = read_text_cached(candidate, max_bytes=None)
        if text is None:
            problems.append("文件不是可读 UTF-8 文本，无法检查内容")
            entry["ast"] = "error"
            return entry
        samples = _secret_samples(text, stats)
        entry["secret_samples"] = samples
        entry["secret_hits"] = len(samples)
        # 安全门禁不可降级为 Agent 警告：疑似凭据命中必须先移除或替换。
        for sample in samples:
            problems.append(
                f"疑似硬编码敏感信息: 第{sample['line']}行（{sample['source']}）"
            )
        if candidate.suffix == ".py":
            tree = ast.parse(text, filename=str(candidate))
            visitor = _DangerousNodeVisitor()
            visitor.visit(tree)
            entry["dangerous_calls"] = list(visitor.failures)
            entry["ast"] = "failed" if visitor.failures else "ok"
            problems.extend(visitor.failures)
        else:
            entry["ast"] = "n/a"
    except (OSError, SyntaxError) as error:
        entry["ast"] = "error"
        problems.append(f"文件检查失败: {error}")
    return entry


def _blocked(reason: str, carrier: str) -> dict[str, Any]:
    """工作区无法定位 carrier 时的证据结构：同样是"原因 + 已有检查结论"。"""
    return {
        "passed": False,
        "reason": f"无法定位目标 carrier: {reason}",
        "failures": [{"file": carrier, "reason": reason}],
        "checks": list(_CHECKS),
        "scope": {"carrier": carrier, "allowed_root": "", "baseline_files": 0},
        "claim": {},
        "totals": {"files": 0, "added": 0, "removed": 0},
        "files": [],
    }


def _summary(failures: list[dict[str, str]], limit: int = 5) -> str:
    """把逐条失败原因压成一句话结论，供后续步骤和重试反馈直接展示。"""
    if not failures:
        return "确定性检查通过"
    parts = [f"{item.get('file', '')}: {item.get('reason', '')}" for item in failures[:limit]]
    extra = len(failures) - len(parts)
    if extra > 0:
        parts.append(f"另有 {extra} 条失败")
    return "确定性检查失败: " + "; ".join(parts)


__all__ = ["validate_changed_files"]
