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


def test_transient_claude_stream_failure_becomes_manual_result_after_bounded_retry(monkeypatch, tmp_path):
    import app.agents.locate.provider as provider_module
    from app.agents.locate.config import LocateConfig
    from app.application.contracts import AgentRequest

    config = LocateConfig.from_dict({
        "provider": "claude_query",
        "writable": False,
        "tools": ["Read", "Glob", "Grep"],
        "max_turns": 14,
        "max_tool_calls": 12,
        "max_context_chars": 50000,
        "max_candidate_files": 6,
        "timeout_seconds": 10,
    })
    carrier_root = tmp_path / "gathers" / "ExampleCarrier"
    carrier_root.mkdir(parents=True)
    request = AgentRequest(
        agent_role="locate",
        run_id="run-1",
        task_id="task-1",
        attempt=1,
        prompt_context={"carrier": "ExampleCarrier", "framework": "gathers"},
        input_evidence={},
        budget={"max_turns": 14},
        stage="locate",
        workspace=str(tmp_path),
        system_prompt="read only",
        prompt="problem",
        prompt_version="v1",
        prompt_hash="hash",
        model="test-model",
        allowed_read_roots=(str(carrier_root),),
        allowed_write_roots=(),
    )
    calls = {"count": 0}

    def fail_stream(*_args, **_kwargs):
        calls["count"] += 1
        raise RuntimeError("Claude Code returned an error result: success")

    monkeypatch.setattr(provider_module, "run_agent_once", fail_stream)
    monkeypatch.setattr("time.sleep", lambda *_args, **_kwargs: None)

    result = provider_module.ClaudeQueryLocateProvider(config).run(request)
    payload = json.loads(result["result"])

    assert calls["count"] == 3
    assert result["passed"] is False
    assert payload["status"] == "MANUAL"
    assert "3 次尝试" in payload["summary"]
