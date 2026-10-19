import json
from pathlib import Path

import workbench.api as workbench_api
from app.entrypoint import _write_run_report


class _EmptyStore:
    def __init__(self, root: Path):
        self.root = root

    def run_dir(self, run_id: str) -> Path:
        path = self.root / run_id
        path.mkdir(parents=True, exist_ok=True)
        steps_path = path / "steps.json"
        if not steps_path.exists():
            steps_path.write_text(json.dumps({"tasks": []}), encoding="utf-8")
        return path

    def steps_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "steps.json"

    def task_dir(self, run_id: str, task_id: str) -> Path:
        return self.run_dir(run_id) / "tasks" / task_id

    def list_steps(self, run_id: str):
        return []


def test_run_report_always_explains_empty_run(tmp_path):
    store = _EmptyStore(tmp_path)
    _write_run_report(store, "empty-run")
    path = store.run_dir("empty-run") / "reports" / "final.json"
    report = json.loads(path.read_text(encoding="utf-8"))

    assert report["result"] == "NO_TASKS"
    assert report["terminal_step"]
    assert report["terminal_reason"].strip()
    assert report["next_action"].strip()


def test_run_report_captures_unhandled_workflow_exception(tmp_path):
    store = _EmptyStore(tmp_path)
    _write_run_report(store, "failed-run", error=RuntimeError("graph construction failed"))
    path = store.run_dir("failed-run") / "reports" / "final.json"
    report = json.loads(path.read_text(encoding="utf-8"))

    assert report["result"] == "FAILED"
    assert report["terminal_step"] == "workflow"
    assert "RuntimeError" not in report["terminal_reason"]
    assert "graph construction failed" not in json.dumps(report, ensure_ascii=False)
    assert "后台工作流异常" in report["terminal_reason"]
    failure = json.loads((store.run_dir("failed-run") / "reports" / "run_failure.json").read_text(encoding="utf-8"))
    assert failure["error_type"] == "RuntimeError"
    assert "graph construction failed" not in json.dumps(failure, ensure_ascii=False)
    assert "后台工作流异常" in failure["message"]
    assert report["next_action"].strip()


def test_background_failure_preserves_task_level_report_summary(tmp_path, monkeypatch):
    run_dir = tmp_path / "run-with-task"
    report_dir = run_dir / "reports"
    report_dir.mkdir(parents=True)
    expected_tasks = [{
        "task_id": "task-1", "carrier": "CouriersPlease", "biztype": "framework.track_server",
        "result": "MANUAL", "terminal_step": "prepare_evidence",
        "terminal_reason": "missing gather source entry point", "next_action": "review source layout",
    }]
    (report_dir / "final.json").write_text(json.dumps({
        "report_version": 1, "report_kind": "run_final", "run_id": "run-with-task",
        "result": "MANUAL", "status": "MANUAL", "task_count": 1, "task_results": expected_tasks,
    }), encoding="utf-8")

    class Store:
        def run_dir(self, run_id: str) -> Path:
            return run_dir

    monkeypatch.setattr(workbench_api, "_store", lambda: Store())
    workbench_api._record_background_failure("run-with-task", RuntimeError("background error"))

    report = json.loads((report_dir / "final.json").read_text(encoding="utf-8"))
    assert report["result"] == "FAILED"
    assert report["task_count"] == 1
    assert report["task_results"] == expected_tasks
    assert "后台工作流异常" in report["terminal_reason"]
    assert "background error" not in json.dumps(report, ensure_ascii=False)
