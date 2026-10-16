import json

from app.agents.locate.provider import _is_max_turns_error, _max_turns_fallback


def test_max_turns_error_is_classified_without_matching_unrelated_failures():
    assert _is_max_turns_error(
        RuntimeError("Claude Code returned an error result: Reached maximum number of turns (8)")
    )
    assert _is_max_turns_error(RuntimeError("error_max_turns"))
    assert not _is_max_turns_error(RuntimeError("connection reset"))


def test_max_turns_fallback_is_structured_and_preserves_public_events():
    error = RuntimeError("Reached maximum number of turns (8)")
    error.agent_events = [
        {"type": "tool_use", "tool": "Read", "input": {"file_path": "gathers/X/main.py"}},
        {"type": "tool_result", "text": "source snippet"},
    ]

    result = _max_turns_fallback(error, 8)
    payload = json.loads(result["result"])

    assert result["passed"] is False
    assert result["usage"]["tool_calls"] == 1
    assert result["messages"] == error.agent_events
    assert payload["status"] == "INSUFFICIENT_EVIDENCE"
    assert payload["recommended_scope"] == "manual"
    assert payload["source_evidence"] == []
    assert "达到最大交互回合数 8" in payload["summary"]
