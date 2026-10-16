import json
from pathlib import Path

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
    assert "graph construction failed" in report["terminal_reason"]
    assert report["next_action"].strip()
