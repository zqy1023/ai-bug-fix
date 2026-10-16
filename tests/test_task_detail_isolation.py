from pathlib import Path
from types import SimpleNamespace

import workbench.api as workbench_api


def test_task_detail_endpoint_isolated_to_requested_task(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs" / "run-1"
    task_a_dir = run_dir / "tasks" / "task-a"
    task_a_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text('{"log_days": 1}', encoding="utf-8")

    class Store:
        def run_dir(self, run_id: str) -> Path:
            return run_dir

        def task_dir(self, run_id: str, task_id: str) -> Path:
            return run_dir / "tasks" / task_id

    payload = {
        "run_id": "run-1",
        "status": "partial_failed",
        "tasks": [
            {
                "task_id": "task-a", "carrier": "CouriersPlease", "biztype": "framework.track_server",
                "status": "MANUAL", "approval_pending": False, "problem": {"error": "missing source evidence"},
                "steps": [{"name": "prepare_evidence", "status": "succeeded", "result": "MANUAL",
                           "output": {"legacy_only": True, "legacy_reason": "only legacy source is available"}}],
                "report": {"result": "MANUAL", "terminal_step": "prepare_evidence",
                           "terminal_reason": "only legacy source is available",
                           "next_action": "inspect the real source entry point"},
            },
            {
                "task_id": "task-b", "carrier": "OtherCarrier", "biztype": "track_server",
                "status": "SUCCESS", "approval_pending": False,
                "steps": [{"name": "repair_agent", "status": "succeeded", "result": "SUCCESS"}],
                "report": {"result": "SUCCESS", "terminal_reason": "fixed"},
            },
        ],
    }
    monkeypatch.setattr(workbench_api, "_store", lambda: Store())
    monkeypatch.setattr(workbench_api, "_run_payload", lambda store, run_id: payload)
    monkeypatch.setattr(workbench_api, "_v1_run_summary", lambda _: {"status": "partial_failed"})

    result = workbench_api.v1_get_task("run-1", "task-a", None)

    assert result["task"]["task_id"] == "task-a"
    assert result["task"]["report"]["terminal_step"] == "prepare_evidence"
    assert "only legacy source is available" in result["task"]["report"]["terminal_reason"]
    assert all(step["name"] != "repair_agent" for step in result["task"]["steps"])
    assert all(artifact.get("task_id") == "task-a" for artifact in result["artifacts"] if artifact.get("task_id"))
