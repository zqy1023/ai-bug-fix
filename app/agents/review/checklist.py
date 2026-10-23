"""由真实上游结果生成分项状态，模型不改写确定性检查结论。"""

from typing import Any

from app.workflow.steps.verify_issue.assessment import verification_is_complete, verification_for_review


def review_checks(state: dict[str, Any], code_verdict: str, coverage: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """区分未验证与失败，每行保留一句可操作说明。"""
    gate = state.get("gate_result") or {}
    verify = verification_for_review(state)
    regression = state.get("regression_result") or {}
    coverage = coverage or {}
    versions = [(state.get(name) or {}).get("evidence_version") or {}
                for name in ("gate_result", "verify_result", "regression_result")]
    version_ok = all(v.get("patch_id") for v in versions) and len({v["patch_id"] for v in versions}) == 1
    rows = [
        ("scope", "范围与变更检查", "passed" if state.get("check_passed") else "failed" if gate else "unverified",
         "只修改当前运输商，变更检查通过" if state.get("check_passed") else str(gate.get("reason") or "尚无变更检查结果")),
        ("syntax", "语法检查", "passed" if state.get("syntax_passed") else "unverified",
         "Python 语法检查通过" if state.get("syntax_passed") else "缺少已通过的语法结果"),
        ("code", "代码逻辑审查", {"PASS": "passed", "FAIL": "failed"}.get(code_verdict, "unverified"),
         {"PASS": "未发现已确认代码缺陷", "FAIL": "发现代码缺陷，见下方待修复项"}.get(code_verdict, "AI 尚未确认代码逻辑，需人工复核")),
        ("issue", "原始问题验证", "passed" if verification_is_complete(verify) else "failed" if verify.get("status") == "failed" else "unverified",
         str(verify.get("reason") or "尚未取得原始问题的验证结果")),
        ("regression", "业务请求与补充回归", "passed" if regression.get("status") == "verified" and regression.get("passed") else
         str(regression.get("status")) if regression.get("status") in {"syntax_only", "not_configured", "not_executed", "failed"} else "unverified",
         str(regression.get("reason") or "未取得补充回归执行记录")),
        ("coverage", "本次变更的验证覆盖", {"sufficient": "passed", "insufficient": "unverified"}.get(str(coverage.get("decision")), "unverified"),
         str(coverage.get("reason") or "尚未对照实际变更评估覆盖，不能因语法通过而自动豁免")),
        ("version", "证据与补丁版本", "passed" if version_ok else "unverified",
         "三项证据对应同一补丁哈希" if version_ok else "证据版本缺失或不一致，需要重新检查"),
    ]
    requirements = regression.get("validation_requirements") or verify.get("validation_requirements") or []
    if requirements:
        failed = [r for r in requirements if r.get("status") == "failed"]
        missing = [r for r in requirements if r.get("status") != "passed"]
        rows.append(("requirements", "变更验证要求", "failed" if failed else "unverified" if missing else "passed",
                     f"{len(requirements) - len(missing)}/{len(requirements)} 项已取得样本与断言执行证据；" +
                     ("存在失败项" if failed else "仍有未验证条件" if missing else "全部通过")))
    return [{"key": key, "label": label, "status": status, "detail": detail[:280]} for key, label, status, detail in rows]
