"""状态、恢复和跨 Run 资源隔离的回归测试，不连接外部服务。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from git import Repo
from langgraph.checkpoint.memory import InMemorySaver

from app.application.audit import StepRecord
from app.application.services import WorkflowServices
from app.domain.errors import DeliveryError, StepFailed
from app.infrastructure.records.lifecycle import LifecycleStore
from app.infrastructure.records.store import RecordStore
from app.workflow.runner import bind


def state_for(tmp_path: Path, task_id: str = "task") -> dict[str, Any]:
    """构建没有网络依赖的真实任务身份。"""
    task = {"task_id": task_id, "carrier": "Carrier", "biztype": "track", "service_name": "test", "problem_key": "issue"}
    return {"run_id": "run", "task_id": task_id, "task": task, "problem": {},
            "source_repo_path": str(tmp_path), "ai_workspace_path": str(tmp_path),
            "attempt": 1, "max_attempts": 1, "review_mode": "agent"}


def test_unverified_result_continues_without_claiming_business_success(tmp_path: Path) -> None:
    from app.workflow.routes import REGRESSION, after_validate_changes
    from app.workflow.steps.verify_issue import SPEC

    store = RecordStore(tmp_path)
    services = WorkflowServices(metadata={"independent_verify": lambda _: {"passed": False, "status": "unverified"}})
    patch = bind(SPEC, store, services)(state_for(tmp_path))
    assert patch["last_result"] == "passed"
    assert patch["verify_passed"] is False
    assert patch["retry_target"] == ""
    assert patch["manual_confirmation_required"] is True
    assert after_validate_changes(patch) == REGRESSION
    assert store.load("run", "verify_issue", "task").result == "UNVERIFIED"


def test_analysis_delivery_is_terminal_only_when_subgraph_finishes(tmp_path: Path) -> None:
    store = RecordStore(tmp_path)
    lifecycle = LifecycleStore(tmp_path)
    store.save("run", StepRecord(name="finalize_execution", title="final", status="succeeded", result="MANUAL",
                                output={"delivery_status": "MANUAL", "terminal_reason": "manual"}), "task")
    store.save("run", StepRecord(name="prepare_delivery", title="delivery", status="succeeded"), "task")
    assert lifecycle.read_task("run", "task")["status"] == "running"
    store.complete_task("run", "task")
    assert lifecycle.read_task("run", "task")["status"] == "manual"


def test_task_permission_error_does_not_abort_next_task(tmp_path: Path) -> None:
    from app.workflow.definitions import TASK_NODE_NAMES
    from app.workflow.graph import build_graph, build_task_graph, collect
    from app.workflow.routes import FINALIZE, IDENTIFY, LOCATE, PREPARE_CONTEXT, RUN_PREPARE, SOURCE_SYNC
    from app.workflow.steps.finalize_execution import SPEC as final_spec
    from app.workflow.steps.prepare_source_snapshot import SPEC as snapshot_spec

    store = RecordStore(tmp_path)
    services = WorkflowServices()

    def fail_snapshot(*_: Any) -> Any:
        raise PermissionError("snapshot directory is busy")

    failing = bind(replace(snapshot_spec, runner=fail_snapshot), store, services)
    nodes = {name: lambda _: {"last_result": "passed"} for name in TASK_NODE_NAMES}
    nodes[PREPARE_CONTEXT] = lambda state: failing(state) if state["task_id"] == "bad" else {"last_result": "passed"}
    nodes[LOCATE] = lambda _: {"last_result": "manual", "terminal_status": "MANUAL"}
    nodes[FINALIZE] = bind(final_spec, store, services)
    task_graph = build_task_graph(nodes)
    inputs = [state_for(tmp_path, task_id)["task"] for task_id in ("bad", "good")]
    run_nodes = {SOURCE_SYNC: lambda _: {"last_result": "passed"}, RUN_PREPARE: lambda _: {"last_result": "passed"},
                 IDENTIFY: lambda _: {"last_result": "passed", "tasks": inputs}}

    def collect_task(state: dict[str, Any]) -> dict[str, Any]:
        store.complete_task(state["run_id"], state["task_id"])
        return collect(state)

    graph = build_graph(run_nodes, task_graph, InMemorySaver(), task_collector=collect_task)
    result = graph.invoke(state_for(tmp_path), {"configurable": {"thread_id": "run"}})
    assert result["task_cursor"] == 2
    assert store.load("run", "prepare_source_snapshot", "bad").status == "failed"
    assert LifecycleStore(tmp_path).read_task("run", "bad")["status"] == "failed"
    assert LifecycleStore(tmp_path).read_task("run", "good")["status"] == "manual"
    assert not (store.run_dir("run") / "reports/run_failure.json").exists()


def test_phantom_claim_returns_file_evidence_instead_of_key_error(tmp_path: Path) -> None:
    from app.workflow.steps.check_changes.verification import validate_changed_files

    relative = "gathers/Carrier/main.py"
    for root in (tmp_path / "base", tmp_path / "work"):
        path = root / relative
        path.parent.mkdir(parents=True)
        path.write_text("value = 1\n", encoding="utf8")
    baseline = {relative: hashlib.sha256((tmp_path / "base" / relative).read_bytes()).hexdigest()}
    result = validate_changed_files(tmp_path / "work", "Carrier", [relative], baseline, tmp_path / "base")
    assert result["passed"] is False
    assert any(item["code"] == "PHANTOM_CLAIM" for item in result["failures"])


def test_syntax_skips_only_deletions_confirmed_by_baseline_and_gate(tmp_path: Path) -> None:
    from app.workflow.steps.syntax_regression.models import SyntaxInput
    from app.workflow.steps.syntax_regression.node import run
    from app.workflow.steps.run_regression.regression import _commands_for

    relative = "gathers/Carrier/deleted.py"
    data = SyntaxInput(ai_workspace_path=str(tmp_path), changed_files=[relative], baseline_manifest={relative: "hash"}, deleted_files=[relative])
    assert run(None, data).checked_files == []
    with pytest.raises(StepFailed):
        run(None, data.model_copy(update={"deleted_files": []}))
    assert _commands_for([["python", "-m", "py_compile"]], [relative], tmp_path) == []


def test_run_cleanup_does_not_close_another_runs_session(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.infrastructure import runtime

    monkeypatch.setattr(runtime, "_SESSIONS", {})
    monkeypatch.setattr(runtime, "_MAX_SESSIONS", 2)
    closed: list[str] = []
    with runtime.session_scope("repair", "A", lambda: SimpleNamespace(close=lambda: closed.append("A")), run_id="A", task_id="task"):
        with runtime.session_scope("repair", "B", lambda: SimpleNamespace(close=lambda: closed.append("B")), run_id="B", task_id="task"):
            runtime.close_sessions("A")
            assert closed == []
        assert closed == []
    assert closed == ["A"]
    runtime.close_sessions("B")
    assert closed == ["A", "B"]


def test_active_sessions_are_not_evicted_for_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.infrastructure import runtime

    monkeypatch.setattr(runtime, "_SESSIONS", {})
    monkeypatch.setattr(runtime, "_MAX_SESSIONS", 1)
    closed: list[str] = []
    with runtime.session_scope("repair", "A", lambda: SimpleNamespace(close=lambda: closed.append("A")), run_id="A", task_id="task"):
        with runtime.session_scope("repair", "B", lambda: SimpleNamespace(close=lambda: closed.append("B")), run_id="B", task_id="task"):
            assert not closed
        assert "A" not in closed
    runtime.close_sessions()


def test_checkpoint_graphs_share_saver_and_transaction_lock(tmp_path: Path) -> None:
    from app.workflow.wiring import _checkpoint_saver

    first = _checkpoint_saver(tmp_path)
    second = _checkpoint_saver(tmp_path)
    assert first is second
    assert first.lock is second.lock


def test_shared_checkpoint_saver_handles_concurrent_runs(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from typing_extensions import TypedDict
    from langgraph.graph import END, START, StateGraph
    from app.workflow.wiring import _checkpoint_saver

    class Counter(TypedDict):
        value: int

    graph = StateGraph(Counter)
    graph.add_node("increment", lambda state: {"value": state["value"] + 1})
    graph.add_edge(START, "increment")
    graph.add_edge("increment", END)
    compiled = graph.compile(checkpointer=_checkpoint_saver(tmp_path))

    def execute(index: int) -> int:
        config = {"configurable": {"thread_id": f"run-{index}"}}
        compiled.invoke({"value": index}, config)
        return compiled.get_state(config).values["value"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(execute, range(16))) == list(range(1, 17))


def test_lifecycle_errors_and_finish_times_survive_missing_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import workbench.api as api

    store = RecordStore(tmp_path)
    LifecycleStore(tmp_path).update_run("run", status="failed", error_type="MISSING_TERMINAL_REPORT", error_message="worker exited")
    payload = api._run_payload(store, "run")
    assert payload["finished_at"]
    assert payload["run_error"]["message"] == "worker exited"
    monkeypatch.setattr(api, "_store", lambda: store)
    monkeypatch.setattr(api, "_v1_run_summary", lambda value: value)
    store.save("run", StepRecord(name="prepare_source_snapshot", title="snapshot", status="running"), "task")
    detail = api.v1_get_task("run", "task", None)
    assert detail["run_error"]["message"] == "worker exited"
    assert "syntax_regression" in next(stage for stage in detail["stages"] if stage["key"] == "validate_changes")["members"]


def test_heartbeat_timeout_settles_active_step_and_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import workbench.api as api

    store = RecordStore(tmp_path)
    store.save("run", StepRecord(name="prepare_source_snapshot", title="snapshot", status="running"), "task")
    lifecycle = LifecycleStore(tmp_path)
    lifecycle.update_run("run", status="running", worker_active=True)
    path = lifecycle.run_path("run")
    value = lifecycle.read_run("run")
    value["heartbeat_at"] = "2000-01-01T00:00:00+00:00"
    path.write_text(json.dumps(value), encoding="utf8")
    monkeypatch.setattr(api, "_RUN_LOCKS", {})
    payload = api._run_payload(store, "run")
    assert payload["status"] == "failed"
    assert payload["tasks"][0]["status"] == "FAILED"
    assert store.load("run", "prepare_source_snapshot", "task").status == "failed"
    assert api._run_payload(store, "run")["run_error"]["error_type"] == "WORKER_HEARTBEAT_TIMEOUT"


def test_single_step_preserves_original_run_delivery_policy(tmp_path: Path) -> None:
    from app.application.step_runner import state_from_records

    store = RecordStore(tmp_path)
    store.create_run("run", {"source_repo_path": str(tmp_path), "allow_push": True, "require_push": True, "review_mode": "human", "max_attempts": 7})
    state = state_from_records(store, "run")
    assert state["allow_push"] is True and state["require_push"] is True
    assert state["review_mode"] == "human" and state["max_attempts"] == 7


def test_legacy_failed_run_repairs_active_task_without_overwriting_completed_task(tmp_path: Path) -> None:
    import workbench.api as api

    store = RecordStore(tmp_path)
    store.save("run", StepRecord(name="prepare_source_snapshot", title="snapshot", status="running"), "bad")
    store.save("run", StepRecord(name="finalize_execution", title="final", status="succeeded", result="MANUAL", output={"delivery_status": "MANUAL"}), "done")
    store.save("run", StepRecord(name="prepare_delivery", title="delivery", status="succeeded"), "done")
    store.write_artifact("run", "reports/run_failure.json", {"status": "FAILED", "message": "worker crashed"})
    payload = api._run_payload(store, "run")
    results = {task["task_id"]: task["status"] for task in payload["tasks"]}
    assert results == {"bad": "FAILED", "done": "MANUAL"}


def test_background_failure_report_survives_lifecycle_io_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import workbench.api as api

    store = RecordStore(tmp_path)
    store.run_dir("run").mkdir()
    monkeypatch.setattr(api, "_store", lambda: store)

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise OSError("lifecycle unavailable")

    monkeypatch.setattr(LifecycleStore, "update_run", unavailable)
    api._record_background_failure("run", RuntimeError("worker error"))
    assert store.read_artifact("run", "reports/run_failure.json")["status"] == "FAILED"


def test_snapshot_publish_retries_transient_rename_and_never_overwrites_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workflow.steps.prepare_source_snapshot.node import _publish_snapshot

    temporary = tmp_path / "temporary"
    temporary.mkdir()
    baseline = tmp_path / "baseline"
    rename = Path.rename
    calls = 0

    def busy_once(path: Path, target: Path) -> Path:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("busy")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", busy_once)
    _publish_snapshot(temporary, baseline)
    assert calls == 2 and baseline.is_dir()
    temporary.mkdir()
    with pytest.raises(StepFailed):
        _publish_snapshot(temporary, baseline)
    assert temporary.is_dir() and baseline.is_dir()


def test_delivery_retry_confirms_same_remote_commit_after_verification_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.infrastructure.filesystem.workspace import WorkspaceConfig, WorkspaceManager
    from app.infrastructure.git.delivery import GitBranchDelivery, GitDeliveryConfig
    from app.workflow.steps.prepare_delivery.models import DeliveryInput
    from app.workflow.steps.prepare_delivery.node import run

    source = tmp_path / "source"
    source.mkdir()
    repo = Repo.init(source, initial_branch="main")
    with repo.config_writer() as writer:
        writer.set_value("user", "name", "Test")
        writer.set_value("user", "email", "test@example.invalid")
    relative = "gathers/Carrier/main.py"
    (source / relative).parent.mkdir(parents=True)
    (source / relative).write_text("value = 1\n", encoding="utf8")
    repo.index.add([relative])
    repo.index.commit("baseline")
    remote = Repo.init(tmp_path / "remote.git", bare=True)
    repo.create_remote("origin", str(remote.git_dir))
    repo.git.push("origin", "main")
    work = tmp_path / "ai"
    (work / relative).parent.mkdir(parents=True)
    (work / relative).write_text("value = 2\n", encoding="utf8")
    manager = WorkspaceManager(WorkspaceConfig(root_dir=tmp_path / "workspaces", project_subdir=Path(".")))
    adapter = GitBranchDelivery(GitDeliveryConfig(source_repo=source, push=True, workspace_manager=manager))
    real_verify = adapter._verify_remote_branch
    calls = 0

    def fail_once(*args: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise DeliveryError("confirmation temporarily unavailable", status="GIT_PUSH_UNVERIFIED", commit=args[-1])
        real_verify(*args)

    monkeypatch.setattr(adapter, "_verify_remote_branch", fail_once)
    store = RecordStore(tmp_path / "runtime")
    ctx = SimpleNamespace(store=store, run=SimpleNamespace(run_id="run"), services=SimpleNamespace(delivery=adapter))
    data = DeliveryInput(task_id="task", carrier="Carrier", repair_branch="ai-bug-tasks/track/Carrier-issue", source_repo_path=str(source), ai_workspace_path=str(work), changed_files=[relative], allow_push=True, review_decision="PASS")
    with pytest.raises(StepFailed):
        run(ctx, data)
    receipt = store.read_artifact("run", "delivery/operation.json", "task")
    output = run(ctx, data)
    assert output.commit == receipt["commit"]
    assert output.pushed is True
    assert remote.commit(f"refs/heads/{data.repair_branch}").hexsha == output.commit
    (work / relative).write_text("value = 3\n", encoding="utf8")
    with pytest.raises(StepFailed):
        run(ctx, data)
    assert remote.commit(f"refs/heads/{data.repair_branch}").hexsha == output.commit
    repo.close()
    remote.close()
