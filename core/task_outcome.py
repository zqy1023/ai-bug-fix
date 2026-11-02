"""任务展示结果：与 Agent 判定、步骤执行状态分开，只认实际交付事实。"""
from typing import Any

TERMINAL_OUTCOMES = frozenset({"CHANGED_DELIVERED", "UNCHANGED_DELIVERED", "SKIPPED", "FAILED"})


def task_outcome(task: dict[str, Any]) -> str:
    report = task.get("report") or {}
    life = task.get("lifecycle") or {}
    status = str(life.get("status") or task.get("status") or task.get("result") or "PENDING").upper()
    if status in {"PENDING", "QUEUED", "WAITING", "NOT_STARTED"}:
        return "PENDING"
    if status in {"RUNNING", "RETRYING", "CANCEL_REQUESTED", "PAUSE_REQUESTED"}:
        return "RUNNING"
    if status == "PAUSED":
        return "PAUSED"
    if status in {"FAILED", "FAIL", "REJECTED", "PARTIAL_FAILED"}:
        return "FAILED"
    steps = list({step.get("name"): step for step in task.get("steps") or []}.values())
    review_step = next((step for step in steps if step.get("name") == "review_agent"), {})
    review = review_step.get("output") or report.get("review") or {}
    if review and (review.get("contract_error") or str(review.get("verdict") or "").upper() == "FAIL"
                   or review.get("code_verdict") in {"FAIL", "UNKNOWN"}):
        return "FAILED"
    # 「业务维度 unverified」不再单独判失败。
    #
    # 复现是审查的一环，不是交付门槛。业务维度 unverified 只说明"这段行为没有
    # 被真实执行检验过"，可能是采集环境未就绪、原始请求缺字段，也可能是审查方
    # 缺业务语义依据。它不构成"修复错了"的证据——真正的失败信号是
    # code_verdict=FAIL/UNKNOWN、三维度出现 failed、findings/required_changes 非空，
    # 这些都已在上面的判定与 delivery_allowed 里处理。
    # 复现结果由 ``reproduction`` 如实记录，并写进交付文档。
    if str(review.get("verdict") or "").upper() == "MANUAL":
        from agents.review.contract import delivery_allowed, judge
        # 只有审查结论本身协议完备时，才用它的放行判定覆盖交付事实；
        # 历史记录里 review 可能只有局部字段（缺 dimensions/coverage），
        # 那种情况不能凭不完整的结论判失败，继续按交付事实推导。
        if judge(review).valid and not delivery_allowed(review):
            return "FAILED"
    failed_agent = bool(report.get("repair_failure_kind")) or review_step.get("status") == "failed" or any(
                       step.get("name") in {"unified_fixer_agent", "review_agent"}
                       and ((step.get("output") or {}).get("failure_kind")
                            or str((step.get("output") or {}).get("contract_error") or "").startswith(
                                ("review_invocation_failed:", "review_protocol_invalid:")))
                       for step in steps)
    if failed_agent:
        return "FAILED"
    from agents.review.contract import judge
    from core.disposition import (
        model_skip_proposal,
        skip_approved_by_review,
        unresolved_blocks_delivery,
    )

    repair_step = next((step for step in steps if step.get("name") == "unified_fixer_agent"), {})
    repair = repair_step.get("output") or {
        "status": report.get("locate_status"), "attribution_type": report.get("attribution_type"),
        "skip_reason": report.get("skip_reason"),
    }
    # 判定统一走 core.disposition：模型跳过是"提议"，落终态标签不能替它背书，
    # 也不能掩盖后续的审查否决。平台确定性跳过不需要模型审查。
    if model_skip_proposal(repair) and not skip_approved_by_review(repair, review):
        return "FAILED"
    if review.get("review_kind") == "analysis_disposition" and not judge(review).passed:
        return "FAILED"
    if unresolved_blocks_delivery(review):
        return "FAILED"
    if report.get("skip_reason") or repair.get("skip_reason"):
        return "SKIPPED"
    if status in {"SKIPPED", "CANCELLED", "CANCELED"}:
        return "SKIPPED"
    if status in {"CHANGED_DELIVERED", "UNCHANGED_DELIVERED"}:
        return status
    published = next((step for step in reversed(steps)
                      if step.get("name") in {"submit_changes", "publish_delivery"}), {})
    # prepared 候选也可能含 commit；只有成功发布节点才能作为交付证明。
    delivery = (published.get("output") or {}) if published else report.get("delivery") or {}
    published_ok = (published.get("status") in {"succeeded", "skipped"}) if published else report.get("delivery_confirmed") is True
    delivered = published_ok and bool(delivery.get("commit")) and bool(delivery.get("pushed") or delivery.get("local_branch"))
    if delivered:
        candidate = next((step for step in reversed(steps)
                          if step.get("name") in {"generate_report", "prepare_delivery"}), {})
        analysis_only = (delivery.get("analysis_only") is True
                         or delivery.get("delivery_phase") == "ANALYSIS_ONLY"
                         or (published.get("input") or {}).get("analysis_only") is True
                         or (candidate.get("input") or {}).get("analysis_only") is True)
        if analysis_only:
            return "UNCHANGED_DELIVERED"
        review = report.get("review") or {}
        if review.get("code_verdict") and review["code_verdict"] != "PASS":
            return "FAILED"
        return "CHANGED_DELIVERED" if report.get("changed_files") else "UNCHANGED_DELIVERED"
    if published and published.get("status") == "failed":
        return "FAILED"
    if review:
        from agents.review.contract import delivery_allowed
        if not delivery_allowed(review):
            return "FAILED"
    if any(step.get("status") == "failed" for step in steps):
        return "FAILED"
    if published or any(step.get("name") in {"generate_report", "prepare_delivery"}
                        and (step.get("output") or {}).get("commit") for step in steps):
        return "FAILED"
    # 正常结束但不具备修复条件的任务记跳过，具体原因保留在原报告。
    if status in {"MANUAL", "INSUFFICIENT_EVIDENCE", "UNVERIFIED", "BLOCKED"}:
        return "SKIPPED"
    return "FAILED"
