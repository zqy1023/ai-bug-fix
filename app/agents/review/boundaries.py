"""从实际 AST 比较无单守卫，防止仅凭旧样本成功就批准扩大分类条件。"""

from __future__ import annotations

import ast
from pathlib import Path


def not_found_guard_changes(workspace: str, baseline: str, files: list[str]) -> list[str]:
    """只报告已改变的无单分支条件；风险需要人工/补充边界证据，不直接认定代码错误。"""
    if not workspace or not baseline:
        return []

    def guards(root: str, relative: str) -> dict[str, list[ast.expr]]:
        base = Path(root).resolve()
        path = (base / relative).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            return {}
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        found: dict[str, list[ast.expr]] = {}
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if isinstance(node, ast.If) and any(
                    isinstance(value, ast.Attribute) and value.attr in {"NotFound", "not_found"}
                    for statement in node.body for value in ast.walk(statement)
                ):
                    found.setdefault(function.name, []).append(node.test)
        return found

    changed = []
    for relative in files:
        if not relative.endswith(".py"):
            continue
        before, after = guards(baseline, relative), guards(workspace, relative)
        for name in sorted(before.keys() | after.keys()):
            old, new = before.get(name, []), after.get(name, [])
            def terms(node: ast.expr) -> int:
                return len(node.values) if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And) else 1
            weakened = len(old) != len(new) or any(
                ast.dump(left) != ast.dump(right) and terms(right) < terms(left)
                for left, right in zip(old, new)
            )
            if weakened:
                changed.append(f"{relative}:{name} 的 NotFound 条件变化：需核对明确无单字段、正常状态及状态冲突边界；单个异常样本通过不足以批准")
    return changed


def datetime_reset_risks(workspace: str, baseline: str, files: list[str]) -> list[str]:
    """识别新引入的日期模型清空但保留 raw 风险，不能靠模型自述判代码通过。"""
    if not workspace or not baseline:
        return []

    def resets(root: str, relative: str) -> set[str]:
        base = Path(root).resolve()
        path = (base / relative).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            return set()
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        found: set[str] = set()
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            parsed = {target.id for node in ast.walk(function) if isinstance(node, ast.Assign)
                      and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
                      and node.value.func.attr == "parser_datetime" for target in node.targets if isinstance(target, ast.Name)}
            for node in ast.walk(function):
                if not isinstance(node, ast.If):
                    continue
                cleared: dict[str, set[str]] = {}
                for statement in node.body:
                    if not (isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Constant)
                            and statement.value.value in (None, "")):
                        continue
                    for target in statement.targets:
                        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id in parsed:
                            cleared.setdefault(target.value.id, set()).add(target.attr)
                found.update(f"{function.name}:{name}" for name, fields in cleared.items()
                             if {"datetime", "format"} <= fields and "raw" not in fields)
        return found

    risks = []
    for relative in files:
        if relative.endswith(".py"):
            risks.extend(f"{relative}:{name} 清空 datetime/format 但保留 raw：须核对真实 formatter 及目标时间错误，现有材料不能确认代码通过"
                         for name in sorted(resets(workspace, relative) - resets(baseline, relative)))
    return risks
