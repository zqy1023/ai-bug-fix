"""将验证目标、执行事实和覆盖边界分开，供页面和审查读取同一份契约。"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

CONTRACT_VERSION = 4


def verification_for_review(state: dict[str, Any]) -> dict[str, Any]:
    """新采集样本只有完整双侧证据且同补丁时才能补足原先缺正文的验证。"""
    original = state.get("verify_result") or {}
    supplemental = (state.get("regression_result") or {}).get("supplemental_verification") or {}
    if (original.get("status") == "unverified"
        and (verification_is_complete(supplemental) or supplemental.get("status") == "failed")
        and (supplemental.get("sample_source") or {}).get("kind") == "newly_captured_response"
        and bool((supplemental.get("evidence_version") or {}).get("patch_id"))
        and (supplemental.get("evidence_version") or {}).get("patch_id")
        == (original.get("evidence_version") or {}).get("patch_id")):
        return supplemental
    return original


def verification_is_complete(report: dict[str, Any]) -> bool:
    """节点、审查与展示共用通过条件，防止旧布尔值覆盖实际断言。"""
    facts = report.get("verification")
    if not isinstance(facts, dict):
        return False
    assertions = facts.get("assertions")
    return (
        report.get("passed") is True and report.get("status") == "verified"
        and report.get("mode") == "response_replay"
        and facts.get("schema_version") == CONTRACT_VERSION
        and facts.get("scope") == "parser_only"
        and not facts.get("requires_request_validation")
        and isinstance(assertions, list) and len(assertions) == 3
        and all(isinstance(item, dict) and item.get("status") == "passed" for item in assertions)
        and {item.get("key") for item in assertions} == {"input", "baseline", "fixed"}
    )


def changed_functions(workspace: Path, baseline: Path, files: list[str]) -> list[str]:
    """比较函数 AST，识别最终正文回放无法覆盖的请求和初始化修改。"""
    def functions(root: Path, relative: str) -> dict[str, str]:
        """保留函数所属类，忽略格式和注释变化。"""
        target = (root / relative).resolve()
        if not target.is_relative_to(root.resolve()) or not target.is_file():
            return {}
        tree = ast.parse(target.read_text(encoding="utf-8-sig"))
        found: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found[node.name] = ast.dump(node)
            elif isinstance(node, ast.ClassDef):
                for member in node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        found[f"{node.name}.{member.name}"] = ast.dump(member)
        return found

    changed = []
    for relative in files:
        if not relative.endswith(".py"):
            changed.append(f"{relative}:非 Python 文件")
            continue
        before, after = functions(baseline, relative), functions(workspace, relative)
        changed.extend(f"{relative}:{name}" for name in sorted(before.keys() | after.keys())
                       if before.get(name) != after.get(name))
    return changed


def assessment(
    *, verdict: str, passed: bool, functions: list[str], original_error: str,
    response_sha256: str, comparison: dict[str, Any], parser_configuration_changes: list[str] | None = None,
) -> dict[str, Any]:
    """生成简短、可追查的业务验证说明，不把单个异常样本当全量回归。"""
    parser_configuration_changes = parser_configuration_changes or []
    request_changes = [name for name in functions
                       if name.rsplit(".", 1)[-1] in {"read_html", "__init__", "on_finish"}
                       and name not in parser_configuration_changes]
    delivery = comparison.get("input_delivery") or []
    received = len(delivery) == 2 and all(isinstance(value, dict)
                                        and value.get("input_delivered") for value in delivery)
    cases = comparison.get("items") or []
    reproduced = passed or verdict in {"not_fixed", "new_error", "invalid_block_classification"}
    if cases:
        reproduced = any(item.get("verdict") in {
            "fixed", "reclassified", "exception_handled", "not_fixed", "new_error", "invalid_block_classification",
        } for item in cases)
    return {
        "schema_version": CONTRACT_VERSION,
        "goal": "用原始录制正文复现原异常，并验证修改后的返回协议与行为变化",
        "scope": "parser_only",
        "outcome": verdict,
        "original_error": original_error,
        "response_sha256": response_sha256,
        "changed_functions": functions,
        "assertions": [
            {"key": "input", "label": "同一正文到达解析入口", "status": "passed" if received else "unverified"},
            {"key": "baseline", "label": "基线复现原异常及位置", "status": "passed" if reproduced else "unverified"},
            {"key": "fixed", "label": "修复后满足验证目标", "status": "passed" if passed else
             "failed" if verdict in {"not_fixed", "new_error", "regression", "invalid_block_classification"} else "unverified"},
        ],
        "requires_request_validation": bool(request_changes),
        "request_changes": request_changes,
        "parser_configuration_changes": parser_configuration_changes,
        "coverage": ["原始异常样本的解析与框架错误映射"],
        "limitations": ["未验证实时请求恢复", "未执行其他录制样本的行为回归"]
        + (["请求、初始化或收尾逻辑发生变化，最终正文回放无法覆盖"] if request_changes else []),
        "review_focus": "对照样本、实际 diff 与原因，核对局部处理是否符合业务规则；排除吞异常、泛化兜底和错误无单",
    }


def parser_configuration_changes(workspace: Path, baseline: Path, files: list[str]) -> list[str]:
    """只豁免初始化中的纯日期格式常量表变化；其它调用/副作用保持请求验证要求。"""
    def initializers(root: Path, relative: str) -> dict[str, ast.FunctionDef]:
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            return {}
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        return {f"{node.name}.__init__": member for node in tree.body if isinstance(node, ast.ClassDef)
                for member in node.body if isinstance(member, ast.FunctionDef) and member.name == "__init__"}

    def strip_table(node: ast.FunctionDef) -> str:
        # 仅支持被业务解析器消费的现有 Data_formats 常量表，不豁免任意 self 赋值。
        import copy
        node = copy.deepcopy(node)
        node.body = [statement for statement in node.body if not (
            isinstance(statement, ast.Assign) and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Attribute)
            and isinstance(statement.targets[0].value, ast.Name) and statement.targets[0].value.id == "self"
            and statement.targets[0].attr == "Data_formats"
            and isinstance(statement.value, (ast.List, ast.Tuple))
            and all(isinstance(item, ast.Constant) and isinstance(item.value, str) for item in statement.value.elts)
        )]
        return ast.dump(node)

    safe: list[str] = []
    for relative in files:
        if not relative.endswith(".py"):
            continue
        before, after = initializers(baseline, relative), initializers(workspace, relative)
        for name in before.keys() & after.keys():
            if ast.dump(before[name]) != ast.dump(after[name]) and strip_table(before[name]) == strip_table(after[name]):
                safe.append(f"{relative}:{name}")
    return safe
