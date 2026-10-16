from __future__ import annotations

from app.workflow.routes import (
    DELIVER,
    FINALIZE,
    LOCATE,
    REVIEW,
    RETRY,
    after_approval,
    after_delivery,
    after_finalize,
    after_locate,
    after_prepare_context,
    after_regression,
    after_retry,
    after_review,
)
from app.infrastructure.filesystem.workspace import WorkspaceManager
from langgraph.graph import END


def _state(**overrides):
    state = {
        "last_result": "passed",
        "attempt": 1,
        "max_attempts": 3,
        "terminal_status": "",
        "evidence": {},
        "locate": {},
        "review": {},
    }
    state.update(overrides)
    return state


def test_legacy_evidence_still_runs_root_cause_analysis():
    assert after_prepare_context(_state(
        terminal_status="manual",
        evidence={"legacy_only": True},
    )) == LOCATE


def test_legacy_only_locate_never_enters_repair():
    assert after_locate(_state(
        evidence={"legacy_only": True},
        locate={"status": "LOCATED", "category": "carrier", "recommended_scope": "carrier"},
    )) == FINALIZE


def test_non_carrier_scope_is_analysis_only():
    assert after_locate(_state(
        locate={"status": "LOCATED", "category": "shared", "recommended_scope": "project"},
    )) == FINALIZE


def test_review_pass_delivers_before_final_approval():
    assert after_review(_state(review={"verdict": "PASS"})) == DELIVER
    assert after_regression(_state()) == REVIEW


def test_analysis_report_delivery_ends_without_approval_loop():
    assert after_finalize(_state(
        terminal_status="MANUAL",
        ai_workspace_path="/tmp/isolated-workspace",
        evidence={"legacy_only": True},
    )) == DELIVER
    assert after_delivery(_state(
        evidence={"legacy_only": True},
    )) == END


def test_rejected_final_approval_retries_within_attempt_limit():
    assert after_approval(_state(decision="REJECT", last_result="rejected")) == RETRY
    assert after_retry(_state(retry_target="locate_agent")) == LOCATE


def test_restore_snapshot_restores_both_framework_roots(tmp_path):
    baseline = tmp_path / "baseline"
    workspace = tmp_path / "workspace"
    for path, value in (
        ("gathers/Carrier/main.py", "new baseline"),
        ("spiders/Carrier/legacy.py", "legacy baseline"),
    ):
        target = baseline / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding="utf-8")
    for path, value in (
        ("gathers/Carrier/main.py", "stale new"),
        ("spiders/Carrier/legacy.py", "stale legacy"),
    ):
        target = workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding="utf-8")

    WorkspaceManager.restore_carrier_snapshot(workspace, baseline, "Carrier", "gathers")

    assert (workspace / "gathers/Carrier/main.py").read_text(encoding="utf-8") == "new baseline"
    assert (workspace / "spiders/Carrier/legacy.py").read_text(encoding="utf-8") == "legacy baseline"


def test_workspace_copy_excludes_logs_and_nacos_data(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    (source / "logs").mkdir(parents=True)
    (source / "nacos-data").mkdir()
    (source / "gathers" / "Carrier" / "logs").mkdir(parents=True)
    (source / "gathers" / "Carrier" / "nacos-data").mkdir()
    (source / "gathers" / "Carrier" / "main.py").write_text("code", encoding="utf-8")
    (source / "gathers" / "Carrier" / "logs" / "nested.log").write_text("nested log", encoding="utf-8")
    (source / "gathers" / "Carrier" / "nacos-data" / "store.db").write_text("nested db", encoding="utf-8")
    (source / "gathers" / "Carrier" / "providers.csv").write_text("secret provider", encoding="utf-8")
    (source / "gathers" / "Carrier" / "auth.csv").write_text("secret auth", encoding="utf-8")
    (source / "logs" / "runtime.log").write_text("log", encoding="utf-8")
    (source / "nacos-data" / "store.db").write_text("db", encoding="utf-8")

    WorkspaceManager._copy_source(source, target)

    assert not (target / "logs").exists()
    assert not (target / "nacos-data").exists()
    assert not (target / "gathers" / "Carrier" / "logs").exists()
    assert not (target / "gathers" / "Carrier" / "nacos-data").exists()
    assert not (target / "gathers" / "Carrier" / "providers.csv").exists()
    assert not (target / "gathers" / "Carrier" / "auth.csv").exists()
    assert (target / "gathers" / "Carrier" / "main.py").is_file()
