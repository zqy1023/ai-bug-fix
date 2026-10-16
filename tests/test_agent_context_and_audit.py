import json

from app.agents.common import _audit_events
from app.agents.context import build_context


def test_build_context_keeps_valid_json_when_budget_is_exceeded():
    problem = {
        "service_name": "gather.py.track",
        "biztype": "Brazil",
        "carrier": "ExampleCarrier",
        **{f"field_{index}": "x" * 1000 for index in range(20)},
    }
    result = build_context(
        problem,
        [f"gathers/ExampleCarrier/file_{index}.py" for index in range(8)],
        retry_reason="retry " * 500,
        prior_evidence=["evidence " * 100 for _ in range(8)],
        symbols={f"gathers/ExampleCarrier/file_{index}.py": ["symbol"] * 24 for index in range(8)},
        category="parse",
        max_chars=700,
    )

    payload = json.loads(result)
    assert len(result) <= 700
    assert payload["context_truncated"] is True
    assert payload["problem"]["service_name"] == "gather.py.track"
    assert payload["problem"]["carrier"] == "ExampleCarrier"


def test_audit_events_keep_public_agent_and_tool_events_only():
    events = _audit_events([
        {"type": "assistant", "text": "确认了目标函数"},
        {"type": "tool_use", "tool": "Read", "input": {"file_path": "gathers/X/main.py"}},
        {"type": "tool_result", "text": "line 12: parse response"},
        {"type": "hidden_thinking", "text": "must not be persisted"},
        {"type": "unknown", "text": "discard"},
    ])

    assert [event["type"] for event in events] == ["assistant", "tool_use", "tool_result"]
    assert events[1]["tool"] == "Read"
    assert "must not be persisted" not in json.dumps(events, ensure_ascii=False)
