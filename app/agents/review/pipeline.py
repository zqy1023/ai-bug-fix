"""修复后审查的唯一入口；确定性验证作为 Agent 内部工具执行。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.errors import GraphInterrupt

from app.agents.review.checklist import review_checks
from app.agents.review.models import ReviewView
from app.application.audit import ArtifactRef, StepError, StepRecord
from app.domain.models import utc_now
from app.infrastructure.records.store import RecordStore
from app.infrastructure.redaction import public_exception
from app.workflow.definitions import REVIEW_EVIDENCE_STEPS
from app.workflow.handoff import apply_step_result, outcome_of

REPORT_PATH = "artifacts/review_agent/substeps.json"


class _ReviewStore:
    """将内部检查记录写入审查产物，不向主 steps.json 添加节点。"""

    def __init__(self, store: RecordStore) -> None:
        self.store = store
        self.records: dict[str, StepRecord] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self.store, name)

    def summaries(self) -> list[dict[str, Any]]:
        return [
            {
                "name": record.name,
                "title": record.title,
                "status": record.status,
                "summary": record.summary,
                "execution": record.execution.model_dump(mode="json") if record.execution else None,
                "assessment": record.assessment.model_dump(mode="json") if record.assessment else None,
                "record_path": f"artifacts/review_agent/steps/{record.name}.json",
            }
            for record in self.records.values()
        ]

    def save(self, run_id: str, record: StepRecord, task_id: str = "", **views: Any) -> Any:
        if record.name not in REVIEW_EVIDENCE_STEPS:
            return self.store.save(run_id, record, task_id, **views)
        self.records[record.name] = record.model_copy(deep=True)
        path = f"artifacts/review_agent/steps/{record.name}.json"
        self.store.write_artifact(run_id, path, record.model_dump(mode="json"), task_id)
        self.store.write_artifact(run_id, REPORT_PATH, {"substeps": self.summaries()}, task_id)
        parent = self.store.load(run_id, "review_agent", task_id)
        if parent is not None:
            parent.summary = f"内部检查：{record.title}"
            parent.output = {"substeps": self.summaries(), "review_report_path": REPORT_PATH}
            self.store.save(run_id, parent, task_id)
        return self.store.task_dir(run_id, task_id) / path


def build_review_pipeline(
    store: RecordStore,
    services: Any,
    model_node: Callable[[dict[str, Any]], dict[str, Any]],
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """完整执行和单步重跑共用同一个带证据收集的审查入口。"""
    from app.agents.review.tools.registry import resolve_tool
    from app.workflow.runner import bind

    def node(state: dict[str, Any]) -> dict[str, Any]:
        run_id, task_id = str(state["run_id"]), str(state["task_id"])
        parent = StepRecord(
            name="review_agent", title="修复后代码审查 Agent", status="running",
            started_at=utc_now(), attempt=int(state.get("attempt") or 1),
            summary="正在检查语法、业务修复与影响范围",
        )
        store.save(run_id, parent, task_id)
        internal = _ReviewStore(store)
        current, combined = dict(state), {}
        failure = None
        try:
            for name in REVIEW_EVIDENCE_STEPS:
                patch = bind(resolve_tool(name), internal, services)(current)
                current.update(patch)
                combined.update(patch)
                if str(current.get("terminal_status") or "").upper() == "CANCELLED":
                    parent.status, parent.result = "skipped", "SKIPPED"
                    parent.finished_at = utc_now()
                    parent.output = {"substeps": internal.summaries()}
                    formal = outcome_of(parent.output, execution_status="cancelled", summary="审查已取消")
                    parent.execution, parent.assessment = formal.execution, formal.assessment
                    store.save(run_id, parent, task_id)
                    return {**combined, "last_result": "cancelled", "next_step": "finalize_execution"}
                # 范围/语法不通过时停止发起业务请求，但仍进入代码审查。
                if name in {"check_changes", "syntax_regression", "plan_validation"} and patch.get("last_result") != "passed":
                    break
            if current.get("review_mode") == "human":
                summary = current.get("validation_summary") or {}
                facts = summary.get("dimensions") or {}
                passed = not summary.get("stale") and set(facts) == {"issue", "request"} and all(v.get("status") == "passed" for v in facts.values())
                dimensions = {
                    "syntax": {"status": "passed" if (current.get("syntax_result") or {}).get("passed") else "unverified", "reason": "确定性语法检查结果", "evidence": [REPORT_PATH]},
                    "scope": {"status": "passed" if (current.get("gate_result") or {}).get("passed") else "unverified", "reason": "确定性范围检查结果", "evidence": [REPORT_PATH]},
                    "business": {"status": "passed" if passed else "unverified", "reason": "人工模式按执行证据核对，代码语义仍待人工审查", "evidence": [REPORT_PATH]},
                }
                view = ReviewView(verdict="MANUAL", passed=False, code_verdict="UNKNOWN", dimensions=dimensions,
                                  summary="内部检查已完成，代码审查由人工执行",
                                  coverage={"decision": "unknown", "reason": "等待人工审查", "reviewed_symbols": [], "missing_checks": []})
            else:
                patch = model_node(current)
                current.update(patch)
                combined.update(patch)
                view = ReviewView.model_validate(current.get("review") or {
                    "verdict": "MANUAL", "summary": "审查未取得有效结果", "contract_error": "REVIEW_UNAVAILABLE",
                })
        except GraphInterrupt:
            parent.status = "waiting"
            parent.output = {"substeps": internal.summaries(), "review_report_path": REPORT_PATH}
            store.save(run_id, parent, task_id)
            raise
        except Exception as error:
            reason = public_exception(error, prefix="修复后代码审查执行失败")
            failure = StepError(code="STEP_EXECUTION_FAILED", message=reason, retryable=True)
            view = ReviewView(verdict="MANUAL", code_verdict="UNKNOWN", summary=reason, contract_error="REVIEW_PIPELINE_ERROR")

        checks = view.checks or review_checks(
            {**current, "check_passed": bool((current.get("gate_result") or {}).get("passed")),
             "syntax_passed": bool((current.get("syntax_result") or {}).get("passed"))},
            view.code_verdict, dimensions=view.dimensions.model_dump(mode="json"),
        )
        view = view.model_copy(update={"substeps": internal.summaries(), "review_report_path": REPORT_PATH, "checks": checks})
        # 统一的正式输出只属于 review_agent，内部执行证据由引用访问。
        output = view.model_dump(mode="json")
        store.write_artifact(run_id, REPORT_PATH, {"substeps": internal.summaries()}, task_id)
        saved = store.load(run_id, "review_agent", task_id) or parent
        failure = failure or saved.error
        saved.status, saved.result, saved.summary = "failed" if failure else "succeeded", view.verdict, view.summary
        saved.error = failure
        saved.finished_at = utc_now()
        saved.output = output
        refs = [*saved.artifacts, *(ref for record in internal.records.values() for ref in record.artifacts), ArtifactRef(name="review_substeps", path=REPORT_PATH)]
        saved.artifacts = list({(ref.name, ref.path): ref for ref in refs}.values())
        formal = outcome_of(output, business=view.verdict, summary=view.summary,
                            execution_status="failed" if failure else "succeeded",
                            error=failure.model_dump(mode="json") if failure else None)
        saved.execution, saved.assessment = formal.execution, formal.assessment
        store.save(run_id, saved, task_id)
        patch = {**combined, "review": output, "last_result": "failed" if view.verdict == "FAIL" else "passed",
                 "next_step": "decide_validation"}
        # 内部步骤已有各自产物属主；这里只交接 Agent 自己的最终结论。
        final = apply_step_result(store, current, "review_agent", {"review": output, "last_result": patch["last_result"], "next_step": "decide_validation"}, formal)
        return {**patch, **final}

    return node
