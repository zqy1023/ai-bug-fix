"""保留修复主张，并将执行证据与尚未完成的验证明确分开。"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.application.plans import plan_of


def from_repair(repair: dict[str, Any]) -> list[dict[str, Any]]:
    """计划中的验证条件是待证主张，不是已执行的断言。"""
    plan = plan_of(repair)
    if plan is None:
        return []
    requirements = []
    for edit in plan.edits:
        if not edit.validation_requirements.strip():
            continue
        identity = json.dumps([edit.path, edit.symbol, edit.validation_requirements], ensure_ascii=False)
        requirements.append({"id": hashlib.sha256(identity.encode()).hexdigest()[:16],
                             "path": edit.path, "symbol": edit.symbol,
                             "requirement": edit.validation_requirements,
                             "source": "repair_plan", "status": "unverified",
                             "reason": "修复计划提出的验证条件尚未逐项取得执行证据"})
    return requirements


def assess(requirements: list[dict[str, Any]], report: dict[str, Any],
           version: dict[str, Any]) -> list[dict[str, Any]]:
    """只接受验证器返回且绑定当前补丁、有样本和断言的明确执行收据。"""
    receipts = {str(x.get("id")): x for x in report.get("requirement_results") or [] if isinstance(x, dict)}
    results = []
    for requirement in requirements:
        receipt = receipts.get(str(requirement.get("id")), {})
        assertions = receipt.get("assertions") or []
        if not isinstance(assertions, list):
            assertions = []
        bound = bool(version.get("patch_id")) and (receipt.get("evidence_version") or {}).get("patch_id") == version["patch_id"]
        proven = (bound and bool(receipt.get("sample_source")) and bool(assertions)
                  and all(isinstance(a, dict) and a.get("status") == "passed" for a in assertions))
        failed = (bound and bool(receipt.get("sample_source")) and
                  any(isinstance(a, dict) and a.get("status") == "failed" for a in assertions))
        status = "passed" if receipt.get("status") == "passed" and proven else (
            "failed" if receipt.get("status") == "failed" and failed else "unverified")
        results.append({**requirement, "status": status, "evidence_version": version,
                        "reason": str(receipt.get("reason") or {"passed": "当前补丁的样本与断言均通过",
                                                                 "failed": "当前补丁存在已执行且失败的断言",
                                                                 "unverified": "缺少绑定当前补丁的样本与逐项断言执行证据"}[status]),
                        "evidence": receipt if status != "unverified" else {}})
    return results
