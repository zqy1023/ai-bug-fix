"""最近一轮真实故障的离线回归，不调用模型或外部服务。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.application.audit import StepRecord
from app.infrastructure.records.lifecycle import LifecycleStore
from app.infrastructure.records.store import RecordStore


def test_gate_persists_real_side_by_side_artifact(tmp_path: Path) -> None:
    from app.workflow.steps.check_changes.models import CheckInput
    from app.workflow.steps.check_changes.node import run

    relative = "gathers/Carrier/main.py"
    baseline, workspace = tmp_path / "baseline", tmp_path / "workspace"
    for root, value in ((baseline, 1), (workspace, 2)):
        file = root / relative
        file.parent.mkdir(parents=True)
        file.write_text(f"value = {value}\n", encoding="utf-8")
    emitted = {}
    ctx = SimpleNamespace(emit_artifact=lambda name, path, payload: emitted.update({path: payload}))
    result = run(ctx, CheckInput(carrier="Carrier", ai_workspace_path=str(workspace),
                                changed_files=[relative], baseline_dir=str(baseline),
                                baseline_manifest={relative: hashlib.sha256((baseline / relative).read_bytes()).hexdigest()}))
    assert result.passed
    evidence = emitted["artifacts/diff.json"]["files"][0]
    assert evidence["before"] == "value = 1\n"
    assert evidence["after"] == "value = 2\n"


def test_shared_candidates_and_evidence_use_same_safe_resolution(tmp_path: Path) -> None:
    from app.agents.locate.graph import _validate_output_paths
    from app.agents.locate.models import LocateOutput

    root, shared = tmp_path / "work", tmp_path / "shared"
    carrier = root / "gathers/Carrier"
    carrier.mkdir(parents=True)
    common = shared / "common"
    common.mkdir(parents=True)
    (common / "helper.py").write_text("value = 1", encoding="utf-8")
    output = LocateOutput(status="LOCATED", category="carrier", root_cause="cause", recommended_scope="carrier",
                          candidate_files=["common/helper.py"],
                          source_evidence=[{"path": "common/helper.py", "symbol": "value", "finding": "fact"}])
    _validate_output_paths(root, (str(carrier), str(common)), carrier, output, shared)
    assert output.recommended_scope == "project"
    for path in ("../outside.py", "common/missing.py", "other/carrier.py"):
        output.candidate_files = [path]
        with pytest.raises(ValueError):
            _validate_output_paths(root, (str(carrier), str(common)), carrier, output, shared)


def test_locate_contract_failure_preserves_diagnostic_evidence(tmp_path: Path) -> None:
    from app.agents.locate.graph import locate

    carrier = tmp_path / "gathers/Carrier"
    carrier.mkdir(parents=True)
    (carrier / "main.py").write_text("value = 1", encoding="utf-8")
    payload = {"status": "LOCATED", "category": "carrier", "root_cause": "specific cause",
               "facts": ["observed fact"], "summary": "diagnosis", "recommended_scope": "carrier",
               "candidate_files": ["gathers/Carrier/main.py"],
               "source_evidence": [{"path": "gathers/Carrier/main.py", "symbol": "value", "finding": "evidence"}],
               "change_plan": {"unexpected": True}}
    problem = {"carrier": "Carrier", "biztype": "track", "service_name": "test"}
    result = locate({"run_id": "run", "task_id": "task", "attempt": 1, **problem,
                     "problem": problem, "workspace_path": str(tmp_path), "source_snapshot": {},
                     "evidence": {"framework": "gathers", "carrier_dir": "gathers/Carrier",
                                  "candidates": ["gathers/Carrier/main.py"]}},
                    SimpleNamespace(run=lambda _: {"result": json.dumps(payload)}))
    assert result["status"] == "MANUAL" and not result["passed"]
    assert result["root_cause"] == "specific cause"
    assert result["facts"] == ["observed fact"]
    assert len(result["source_evidence"]) == 1
    assert result["evidence_gaps"]


def test_skipped_failure_keeps_reason_through_finalization(tmp_path: Path) -> None:
    from dataclasses import replace
    from app.application.services import WorkflowServices
    from app.domain.errors import StepFailed
    from app.workflow.runner import bind
    from app.workflow.steps.finalize_execution.node import _terminal_details
    from app.workflow.steps.prepare_source_snapshot import SPEC

    def skip(*_: object) -> None:
        raise StepFailed("repair branch already exists", result="skipped")

    store = RecordStore(tmp_path)
    bind(replace(SPEC, runner=skip), store, WorkflowServices())({
        "run_id": "run", "task_id": "task", "attempt": 1, "source_repo_path": str(tmp_path),
        "task": {"task_id": "task", "carrier": "Carrier", "biztype": "track", "service_name": "test", "problem_key": "issue"},
    })
    record = store.load("run", SPEC.name, "task")
    assert "repair branch already exists" in record.summary
    assert "repair branch already exists" in _terminal_details([record], "SKIPPED", "SKIPPED")[1]


def test_missing_carrier_is_manual_without_retry(tmp_path: Path) -> None:
    from app.application.services import WorkflowServices
    from app.workflow.runner import bind
    from app.workflow.steps.prepare_source_snapshot import SPEC

    store = RecordStore(tmp_path)
    services = WorkflowServices(workspaces=SimpleNamespace(prepare_run_tree=lambda *_: tmp_path))
    patch = bind(SPEC, store, services)({
        "run_id": "run", "task_id": "task", "source_repo_path": str(tmp_path),
        "task": {"task_id": "task", "carrier": "Missing", "biztype": "track", "service_name": "test", "problem_key": "issue"},
    })
    assert patch["last_result"] == "manual"
    assert not patch.get("retry_target")


def test_task_progress_updates_active_run_without_reactivating_paused_run(tmp_path: Path) -> None:
    store, lifecycle = RecordStore(tmp_path), LifecycleStore(tmp_path)
    lifecycle.update_run("run", status="running", worker_active=True)
    store.save("run", StepRecord(name="repair_agent", title="repair", status="running"), "task")
    assert lifecycle.read_run("run")["current_step"] == "repair_agent"
    assert lifecycle.read_run("run")["current_task_id"] == "task"
    lifecycle.update_run("run", status="paused", worker_active=False)
    store.save("run", StepRecord(name="check_changes", title="gate", status="succeeded"), "task")
    assert lifecycle.read_run("run")["status"] == "paused"


def test_explicit_manual_repair_does_not_retry() -> None:
    from app.agents.repair.contract import blocked_reason_of, judge
    from app.workflow.routes import FINALIZE, after_repair

    payload = {"passed": False, "analysis": "missing response", "summary": "Need captured HTML",
               "changed_files": [], "change_plan": {"scope": "manual", "edits": [], "rationale": "Need captured HTML"}}
    assert "Need captured HTML" in blocked_reason_of(payload, judge(payload))
    assert after_repair({"last_result": "manual", "terminal_status": "manual", "attempt": 1, "max_attempts": 3}) == FINALIZE


def test_pause_control_does_not_override_confirmed_stopped_pause(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3
    from workbench import api

    database = tmp_path / "control.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE run_control(run_id TEXT,pause_requested INTEGER,cancel_requested INTEGER)")
        connection.execute("INSERT INTO run_control VALUES('run',1,0)")

    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(api, "_control_db", connect)
    summary = api._v1_run_summary({"run_id": "run", "status": "paused", "worker_active": False})
    assert summary["status"] == "paused"
    assert not summary["worker_active"]
    from app.workflow import wiring

    monkeypatch.setattr(api, "_store", lambda: RecordStore(tmp_path))
    monkeypatch.setattr(api, "_run_payload", lambda *_: {"run_id": "run", "status": "paused", "worker_active": False})
    monkeypatch.setattr(wiring, "build_graph", lambda **_: SimpleNamespace(
        get_state=lambda _: SimpleNamespace(next=("run_task",), tasks=(), config={}, created_at=None, parent_config={})
    ))
    detail = api._v1_detail("run")
    assert detail["status"] == "paused"
    assert detail["checkpoint"]["resumable"]


def test_resume_accepts_pause_projection_while_holding_own_control_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3
    from fastapi import BackgroundTasks
    from workbench import api

    database = tmp_path / "control.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE run_control(run_id TEXT PRIMARY KEY,pause_requested INTEGER,cancel_requested INTEGER,updated_at TEXT)")
        connection.execute("INSERT INTO run_control VALUES('run',1,0,'now')")
    monkeypatch.setattr(api, "_RUN_LOCKS", {})
    monkeypatch.setattr(api, "_control_db", lambda: sqlite3.connect(database))
    monkeypatch.setattr(api, "_audit_control", lambda *_: None)

    def detail(_: str) -> dict:
        assert api._RUN_LOCKS['run'].locked()
        return {"status": "pause_requested", "checkpoint": {"resumable": True}}

    monkeypatch.setattr(api, "_v1_detail", detail)
    background = BackgroundTasks()
    result = api.v1_resume_run("run", background, None)
    assert result["status"] == "resume_requested"
    assert len(background.tasks) == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT pause_requested FROM run_control").fetchone()[0] == 0
    api._RUN_LOCKS['run'].release()
