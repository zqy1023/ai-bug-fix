"""识别源码中新引入或改变条件的 NotFound 出口，提供独立审查关注位置。"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
from typing import Any


def _exits(path: Path) -> Counter[str]:
    if not path.is_file():
        return Counter()
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    exits: Counter[str] = Counter()

    def visit(node: ast.AST, guards: tuple[str, ...]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            guards = (*guards, "function:" + node.name)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            if name.casefold().replace("_", "") == "notfound":
                exits[repr(guards) + ast.dump(node)] += 1
        for field, value in ast.iter_fields(node):
            children = value if isinstance(value, list) else [value]
            branch = guards
            if isinstance(node, (ast.If, ast.IfExp)) and field in {"body", "orelse"}:
                branch = (*guards, field + ":" + ast.dump(node.test))
            for child in children:
                if isinstance(child, ast.AST):
                    visit(child, branch)

    visit(tree, ())
    return exits


def not_found_changes(state: dict[str, Any]) -> list[str]:
    if not state.get("baseline_dir"):
        return []
    before, after = Path(state["baseline_dir"]).resolve(), Path(state["ai_workspace_path"]).resolve()
    changed = []
    for name in state.get("changed_files") or []:
        if Path(name).suffix != ".py":
            continue
        old, new = (before / name).resolve(), (after / name).resolve()
        if not old.is_relative_to(before) or not new.is_relative_to(after):
            raise ValueError("业务语义检查文件越出源码范围")
        try:
            if _exits(new) - _exits(old):
                changed.append(name)
        except (SyntaxError, UnicodeError):
            # 语法失败由独立语法审查处理，不能解析时不宣称业务语义已验证。
            changed.append(name)
    return changed

