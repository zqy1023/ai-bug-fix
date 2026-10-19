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


def test_human_review_and_syntax_only_routes_end_without_ai_review_or_regression():
    from app.workflow.routes import after_validate_changes

    assert after_regression({"last_result": "passed", "review_mode": "human"}) == FINALIZE
    assert after_validate_changes({
        "last_result": "passed", "verify_result": {"mode": "syntax_only"}
    }) == FINALIZE
    assert after_finalize({"review_mode": "human"}) == END
    assert after_finalize({"verify_result": {"mode": "syntax_only"}}) == END


def test_retry_limits_cover_all_supported_attempt_budgets():
    from app.workflow.routes import after_validate_changes

    for max_attempts in (1, 2, 3, 5):
        assert after_validate_changes({
            "last_result": "failed", "attempt": max_attempts, "max_attempts": max_attempts,
        }) == FINALIZE
        if max_attempts > 1:
            assert after_validate_changes({
                "last_result": "failed", "attempt": max_attempts - 1,
                "max_attempts": max_attempts,
            }) == RETRY


def test_retry_budget_limits_actual_repair_agent_invocations():
    from app.workflow.graph import build_task_graph
    from app.workflow.definitions import TASK_NODE_NAMES

    for max_attempts in (1, 2, 3, 5):
        calls = {"repair": 0}

        def ok(_state):
            return {"last_result": "passed"}

        def locate(_state):
            return {
                "last_result": "passed",
                "locate": {"status": "LOCATED", "category": "carrier", "recommended_scope": "carrier"},
            }

        def repair(_state):
            calls["repair"] += 1
            return {"last_result": "failed", "terminal_status": "FAILED"}

        def retry(state):
            return {
                "attempt": int(state.get("attempt") or 1) + 1,
                "last_result": "passed",
                "terminal_status": "",
            }

        def finalize(_state):
            return {"last_result": "failed", "terminal_status": "FAILED"}

        nodes = {name: ok for name in TASK_NODE_NAMES}
        nodes.update({
            "locate_agent": locate,
            "repair_agent": repair,
            "retry_prepare": retry,
            "finalize_execution": finalize,
        })
        graph = build_task_graph(nodes)
        graph.invoke({"attempt": 1, "max_attempts": max_attempts}, config={"recursion_limit": 200})
        assert calls["repair"] == max_attempts


def test_human_review_path_does_not_invoke_review_agent():
    from app.workflow.graph import build_task_graph
    from app.workflow.definitions import TASK_NODE_NAMES

    calls = {"review": 0}

    def prepare(_state):
        return {"last_result": "passed"}

    def locate(_state):
        return {
            "last_result": "passed",
            "locate": {"status": "LOCATED", "category": "carrier", "recommended_scope": "carrier"},
        }

    def validate(_state):
        return {"last_result": "passed", "verify_result": {"mode": "response_replay"}}

    def review(_state):
        calls["review"] += 1
        return {"last_result": "passed", "review": {"verdict": "PASS"}}

    nodes = {name: prepare for name in TASK_NODE_NAMES}
    nodes.update({
        "prepare_context": prepare,
        "locate_agent": locate,
        "validate_changes": validate,
        "review_agent": review,
        "finalize_execution": prepare,
    })
    graph = build_task_graph(nodes)
    graph.invoke({"attempt": 1, "max_attempts": 3, "review_mode": "human"}, config={"recursion_limit": 100})
    assert calls["review"] == 0


def test_delivery_contract_exposes_push_policy_explicitly():
    from app.workflow.steps.prepare_delivery import _input

    payload = _input({
        "task_id": "task-1",
        "task": {"carrier": "Carrier", "biztype": "track"},
        "allow_push": True,
        "require_push": True,
        "locate": {"status": "LOCATED", "category": "carrier", "recommended_scope": "carrier"},
        "evidence": {},
    })
    assert payload["allow_push"] is True
    assert payload["require_push"] is True


def test_regression_result_declares_regression_mode(tmp_path):
    from app.infrastructure.filesystem.workspace import WorkspaceConfig, WorkspaceManager
    from app.workflow.steps.run_regression.regression import RegressionRunner

    result = RegressionRunner(
        WorkspaceManager(WorkspaceConfig(root_dir=tmp_path / "workspaces")), [],
    ).run({"carrier": "Carrier"})
    assert result["passed"] is False
    assert result["mode"] == "regression"


def test_retry_manifest_verification_rejects_modified_or_extra_carrier_files(tmp_path):
    workspace = tmp_path / "workspace"
    target = workspace / "gathers" / "Carrier" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_text("baseline", encoding="utf-8")
    digest = WorkspaceManager._sha256(target)

    assert WorkspaceManager.verify_manifest(workspace, {"gathers/Carrier/main.py": digest}) == (True, [])

    target.write_text("changed", encoding="utf-8")
    valid, mismatches = WorkspaceManager.verify_manifest(
        workspace, {"gathers/Carrier/main.py": digest}
    )
    assert valid is False
    assert mismatches == ["gathers/Carrier/main.py"]

    target.write_text("baseline", encoding="utf-8")
    extra = workspace / "spiders" / "Carrier" / "legacy.py"
    extra.parent.mkdir(parents=True)
    extra.write_text("legacy", encoding="utf-8")
    valid, mismatches = WorkspaceManager.verify_manifest(
        workspace, {"gathers/Carrier/main.py": digest}
    )
    assert valid is False
    assert mismatches == ["spiders/Carrier/legacy.py"]


def test_legacy_route_exports_and_old_agent_wrappers_are_removed():
    import app.workflow.routes as routes
    import app.infrastructure.claude.session as session

    for name in ("after_check_changes", "after_deduplicate", "after_evidence", "after_source_snapshot", "after_verify", "after_workspace"):
        assert not hasattr(routes, name)
    assert not hasattr(session, "run_repair_agent")
    assert not hasattr(session, "run_review_agent")


def test_repair_claim_is_atomic_released_and_expirable(tmp_path):
    from types import SimpleNamespace

    from app.workflow.steps.claim_repair_slot.node import (
        _claim_connection,
        _claim_key,
        _claim_slot,
        release_claim,
    )

    source = tmp_path / "repo"
    source.mkdir()
    store = SimpleNamespace(root=tmp_path / "runtime")
    first = SimpleNamespace(store=store, run=SimpleNamespace(run_id="run-1"))
    second = SimpleNamespace(store=store, run=SimpleNamespace(run_id="run-2"))

    assert _claim_slot(first, str(source), "ai-repair/carrier/key", "problem", "task-1") is True
    assert _claim_slot(second, str(source), "ai-repair/carrier/key", "problem", "task-2") is False
    release_claim(store.root, run_id="run-1", task_id="task-1")
    assert _claim_slot(second, str(source), "ai-repair/carrier/key", "problem", "task-2") is True

    connection = _claim_connection(store.root)
    try:
        connection.execute(
            "UPDATE repair_claims SET expires_at=? WHERE claim_key=?",
            ("2000-01-01T00:00:00+00:00", _claim_key(str(source), "ai-repair/carrier/key", "problem")),
        )
        connection.commit()
    finally:
        connection.close()
    assert _claim_slot(first, str(source), "ai-repair/carrier/key", "problem", "task-1") is True


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
