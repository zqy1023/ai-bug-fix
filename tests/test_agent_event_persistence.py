import json

from app.agents.common import _persist_agent_events


def test_agent_events_are_persisted_under_the_task(tmp_path, monkeypatch):
    from app.infrastructure import config as config_module

    class Config:
        runtime_dir = tmp_path

    monkeypatch.setattr(config_module, "load_config", lambda: Config())
    record = {
        "run_id": "run-123",
        "task_id": "task-456",
        "model_call_id": "call-789",
        "agent_role": "repair",
        "attempt": 2,
    }
    events = [
        {"type": "assistant", "text": "已定位目标函数", "elapsed_ms": 12},
        {"type": "tool_use", "tool": "Read", "input": {"file_path": "gathers/X/main.py"}},
    ]

    relative, count = _persist_agent_events(record, events)

    target = tmp_path / "runs" / "run-123" / relative
    lines = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert relative == "tasks/task-456/audit/agent_events.jsonl"
    assert count == 2
    assert [item["event"]["type"] for item in lines] == ["assistant", "tool_use"]
    assert all(item["model_call_id"] == "call-789" for item in lines)
    assert all(item["task_id"] == "task-456" for item in lines)
