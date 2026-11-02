"""把用户的修复习惯做成确定性检查，而不是只写在提示词里。

审查发现三条最常被纠正的习惯完全没有平台校验：

1. **原地加判断，不拆 helper、不新增函数** —— 只写在 ``rules/coding.md``，
   没有任何 AST 校验。
2. **一条报错日志只追一个入口文件，不全改** —— ``changed_files`` 无上限。
3. **改前先读 ``main.py`` 路由确认分派，再改终端文件** —— 提示词里没有强制步骤。

这里只做**事实提取**，不做拦截：把"本轮新增了几个函数""改了几个文件"
"是否读过入口路由"作为结构化证据写进提示词与审查材料，让模型和审查方
看到具体数字，而不是靠自觉。真正的取舍留给审查 Agent 判断——平台不该用
一条 AST 规则直接否掉一个合理的两文件修复。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

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
        before = {key.split("@", 1)[0] for key in _functions(before_root / name)}
        after = {key.split("@", 1)[0] for key in _functions(after_root / name)}
        for function in sorted(after - before):
            added.append(f"{name}::{function}")
    return added


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
