"""Small, safe projections for the workflow observability UI.

Large prompts, source snippets, request/response bodies and full tool output belong in
artifact files. API list/detail payloads expose only the fields needed to understand state.
"""
from __future__ import annotations

from typing import Any


def _short(value: Any, limit: int = 320) -> Any:
    if isinstance(value, str):
        text = " ".join(value.split())
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
    if isinstance(value, list):
        return [_short(item, limit) for item in value[:8]]
    if isinstance(value, dict):
        return {str(key): _short(item, limit) for key, item in list(value.items())[:12]}
    return value


def _pick(source: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    return {key: _short(source[key]) for key in fields if key in source and source[key] is not None}


def _project_output(name: str, output: Any) -> dict[str, Any]:
    common = ("summary", "reason", "status", "passed", "terminal_reason", "next_action")
    fields: dict[str, tuple[str, ...]] = {
        "locate_agent": common + ("root_cause", "analysis", "category", "recommended_scope", "facts", "source_evidence", "evidence_gaps"),
        "repair_agent": common + ("changed_files",),
        "check_changes": common + ("totals", "claim", "scope", "files"),
        "syntax_regression": common + ("mode", "checked_files", "failures"),
        "verify_issue": common + ("mode", "replay_verdict", "verification", "limitations"),
        "run_regression": common + ("command_count", "request_regression", "supplemental_verification"),
        "review_agent": common + ("verdict", "code_verdict", "findings", "required_changes", "coverage"),
        "prepare_delivery": common + ("delivery_status", "repair_branch", "local_branch", "pushed", "commit", "changed_files"),
        "request_approval": common + ("decision", "approval_pending"),
        "finalize_execution": common + ("result", "terminal_step"),
    }
    selected = _pick(output, fields.get(name, common))
    raw_output = output if isinstance(output, dict) else {}
    if name == "repair_agent" and isinstance(raw_output.get("changed_files"), list):
        selected["changed_files_count"] = len(raw_output["changed_files"])
    if name == "locate_agent":
        evidence = selected.get("source_evidence")
        if isinstance(evidence, list):
            selected["source_evidence"] = [
                _pick(item, ("path", "symbol", "finding", "line", "line_start", "line_end"))
                for item in evidence[:8] if isinstance(item, dict)
            ]
    if name == "check_changes":
        files = selected.get("files")
        if isinstance(files, list):
            selected["files"] = [
                _pick(item, ("path", "status", "added", "removed"))
                for item in files[:20] if isinstance(item, dict)
            ]
        claim = selected.get("claim")
        if isinstance(claim, dict):
            selected["claim"] = _pick(claim, ("actual", "declared", "missing", "extra", "passed", "reason"))
            raw_claim = raw_output.get("claim")
            if isinstance(raw_claim, dict) and isinstance(raw_claim.get("actual"), list):
                selected["claim"]["actual_count"] = len(raw_claim["actual"])
        scope = selected.get("scope")
        if isinstance(scope, dict):
            selected["scope"] = _pick(scope, ("diffable", "baseline_files", "passed", "reason", "violations"))
        totals = selected.get("totals")
        if isinstance(totals, dict):
            selected["totals"] = _pick(totals, ("files", "added", "removed"))
    if name == "verify_issue":
        verification = selected.get("verification")
        if isinstance(verification, dict):
            verification = _pick(verification, ("goal", "assertions", "limitations"))
            assertions = verification.get("assertions")
            if isinstance(assertions, list):
                verification["assertions"] = [
                    _pick(item, ("key", "label", "status", "expected", "actual", "reason", "evidence_ref"))
                    for item in assertions[:8] if isinstance(item, dict)
                ]
            selected["verification"] = verification
    if name == "run_regression":
        request = selected.get("request_regression")
        if isinstance(request, dict):
            request = _pick(request, ("entrypoint", "reason", "items"))
            if isinstance(request.get("items"), list):
                request["items"] = [
                    _pick(item, ("request_number", "error_type", "event_count", "error_msg"))
                    for item in request["items"][:8] if isinstance(item, dict)
                ]
            selected["request_regression"] = request
        supplemental = selected.get("supplemental_verification")
        if isinstance(supplemental, dict):
            selected["supplemental_verification"] = _pick(supplemental, ("status", "reason"))
    if name == "review_agent":
        coverage = selected.get("coverage")
        if isinstance(coverage, dict):
            selected["coverage"] = _pick(coverage, ("decision", "reason", "missing_checks"))
    return selected


def project_step(record: Any, *, include_raw: bool = False) -> dict[str, Any]:
    """Project a StepRecord/model/dict for the UI without leaking bulk inputs by default."""
    if hasattr(record, "model_dump"):
        source = record.model_dump(mode="json")
    elif isinstance(record, dict):
        source = dict(record)
    else:
        return {}
    name = str(source.get("name") or "")
    result: dict[str, Any] = {
        key: source.get(key)
        for key in ("name", "title", "status", "result", "started_at", "finished_at", "duration_ms", "attempt", "summary")
        if source.get(key) is not None
    }
    error = source.get("error")
    if isinstance(error, dict):
        result["error"] = {
            "code": str(error.get("code") or "STEP_EXECUTION_FAILED"),
            "message": _short(error.get("message") or "", 360),
            "retryable": bool(error.get("retryable")),
        }
    artifacts = source.get("artifacts")
    if isinstance(artifacts, list):
        result["artifacts"] = [
            _pick(item, ("name", "path", "media_type", "description", "size_bytes", "task_id"))
            for item in artifacts[:8] if isinstance(item, dict)
        ]
    result["output"] = _project_output(name, source.get("output"))
    if name == "prepare_delivery":
        result["input"] = _pick(source.get("input"), ("analysis_only", "report_paths", "repair_branch"))
    elif include_raw:
        result["input"] = source.get("input") if isinstance(source.get("input"), dict) else {}
    if include_raw:
        result["output"] = source.get("output") if isinstance(source.get("output"), dict) else {}
        result["input"] = source.get("input") if isinstance(source.get("input"), dict) else {}
    return result



def project_problem(problem: Any) -> dict[str, Any]:
    """Return only the incident fields needed in the task header and root-cause card."""
    fields = ("service_name", "carrier", "biztype", "error_type", "error", "raw_error",
              "request_number", "timestamp", "log_excerpt")
    return _pick(problem, fields)


def project_report(report: Any) -> dict[str, Any]:
    """Keep the terminal contract while omitting bulky report payloads from the task API."""
    fields = ("report_version", "report_kind", "run_id", "task_id", "carrier", "biztype",
              "result", "status", "terminal_status", "terminal_step", "terminal_reason",
              "next_action", "finished_at", "elapsed_ms", "tokens", "change_summary",
              "completed_steps", "legacy_report", "service_name")
    return _pick(report, fields)
