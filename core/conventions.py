"""把用户的修复习惯做成确定性检查，而不是只写在提示词里。

审查发现三条最常被纠正的习惯完全没有平台校验：

1. **原地加判断，不拆 helper、不新增函数** —— 只写在 ``rules/coding.md``，
   没有任何 AST 校验。
2. **一条报错日志只追一个入口文件，不全改** —— ``changed_files`` 无上限。
3. **改前先读 ``main.py`` 路由确认分派，再改终端文件** —— 提示词里没有强制步骤。

事实提取供独立审查使用；新增 helper 和既有换行风格由确定性检查约束。
修改文件数量仍作为证据，不能仅凭数量否决有依据的多文件修复。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from collections import Counter

# 路由入口文件名：新框架用 main.py 分派 gather_channel。
ROUTER_FILE = "main.py"


def _functions(path: Path) -> dict[str, int]:
    """统计文件里每个函数的定义位置（行列），用于判断"是否新增函数"。"""
    if not path.is_file():
        return {}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, SyntaxError):
        return {}
    found: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[f"{node.name}@{node.lineno}"] = node.lineno
    return found


def new_functions(baseline_dir: str | Path, workspace: str | Path, changed_files: list[str]) -> list[str]:
    """列出本轮**新增**的函数（基线里不存在的定义）。

    仅按 ``名字@行号`` 比较是不够的——插入几行会把所有函数的行号都推后。
    因此按名字判断：基线里没有这个名字，才算是新增 helper。
    """
    before_root, after_root = Path(baseline_dir), Path(workspace)
    added: list[str] = []
    for name in changed_files:
        if Path(name).suffix != ".py":
            continue
        before = _scoped_functions(before_root / name)
        after = _scoped_functions(after_root / name)
        for function in sorted((after - before).elements()):
            added.append(f"{name}::{function}")
    return added


def _scoped_functions(path: Path) -> Counter[str]:
    if not path.is_file():
        return Counter()
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    found: Counter[str] = Counter()

    def visit(node: ast.AST, parents: tuple[str, ...] = ()) -> None:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            parents = (*parents, node.name)
            if not isinstance(node, ast.ClassDef):
                found[".".join(parents)] += 1
        for child in ast.iter_child_nodes(node):
            visit(child, parents)

    visit(tree)
    return found


def convention_errors(baseline_dir: str, workspace: str, changed_files: list[str]) -> list[str]:
    """客观硬约束不接受模型自批例外；风格偏好仍由独立审查判断。"""
    if not baseline_dir:
        return []
    errors = []
    for item in new_functions(baseline_dir, workspace, changed_files):
        symbol = item.split("::", 1)[1].split(".")
        if not (len(symbol) == 2 and symbol[-1] in {"__init__", "read_html", "process_html", "on_finish"}):
            errors.append(f"禁止新增辅助函数：{item}")
    for name in changed_files:
        before, after = Path(baseline_dir) / name, Path(workspace) / name
        if not before.is_file() or not after.is_file() or after.suffix != ".py":
            continue
        old, new = before.read_bytes(), after.read_bytes()
        if b"\r\n" in old and b"\r\n" not in new:
            after.write_bytes(new.replace(b"\n", b"\r\n"))
    return errors


def convention_facts(
    baseline_dir: str | Path | None,
    workspace: str | Path,
    changed_files: list[str],
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """汇总与修复习惯相关的事实，供提示词与审查材料引用。

    返回字段：

    - ``changed_file_count`` / ``changed_files``：本轮改动规模。
    - ``new_functions``：新增函数清单（用户的硬性习惯是"不新增 helper"）。
    - ``router_file_changed``：是否直接改了 ``main.py`` 路由本身。
    - ``router_readable``：工作区里是否存在可核对的入口路由。
    - ``expectations``：把上述事实翻译成审查要回答的问题，避免只报数字。
    """
    evidence = evidence or {}
    files = [str(item) for item in changed_files]
    new_funcs = new_functions(baseline_dir, workspace, files) if baseline_dir else []
    after_root = Path(workspace)
    carrier = str(evidence.get("carrier_dir") or "")
    router = (after_root / carrier / ROUTER_FILE) if carrier else None
    facts: dict[str, Any] = {
        "changed_file_count": len(files),
        "changed_files": files[:20],
        "new_functions": new_funcs,
        "router_file_changed": any(Path(name).name == ROUTER_FILE for name in files),
        "router_readable": bool(router is not None and router.is_file()),
    }
    expectations = [
        "每处改动是否都在既有方法的出错位置局部完成，没有拆分或新增函数？",
        "若改动涉及多个文件，每处是否都有本次异常的必要性依据，而不是顺手改？",
        "若未核对入口路由就直接改终端实现，请说明如何确认这就是实际执行的入口。",
    ]
    if new_funcs:
        expectations.insert(
            0,
            f"本轮新增了 {len(new_funcs)} 个函数/方法（{', '.join(new_funcs[:5])}）："
            "必须给出本次异常确实无法在既有方法内局部处理的具体理由。",
        )
    if len(files) > 1:
        expectations.insert(
            0,
            f"本轮共修改 {len(files)} 个文件：需逐文件说明改动必要性；"
            "多文件改动必须能指出每处的触发条件。",
        )
    facts["expectations"] = expectations
    return facts


__all__ = ["ROUTER_FILE", "convention_facts", "new_functions"]
