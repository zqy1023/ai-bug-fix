"""SDK owns round counting; permissions and successful tool counts remain local."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from infrastructure.llm.session import _run


def test_fragments_and_scope_rejections_do_not_starve_legal_reads(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    target = source / "main.py"
    target.write_text("value = 1")
    decisions = []
    captured = []
    AssistantMessage = type("AssistantMessage", (), {"content": []})

    class Result:
        is_error = False
        result = "done"
        usage = {}
        subtype = "success"
        structured_output = None

    async def query(*, prompt, options):
        captured.append(options)
        hook = options.hooks["PreToolUse"][0].hooks[0]
        # Text/thinking fragments are not independent model/tool rounds.
        for _ in range(8):
            yield AssistantMessage()
        for path in (tmp_path / "outside1.py", tmp_path / "outside2.py", target, target):
            response = await hook({"tool_name": "Read", "tool_input": {"file_path": str(path)}}, "id", None)
            decisions.append(response["hookSpecificOutput"]["permissionDecision"])
            yield AssistantMessage()
        yield Result()

    config = SimpleNamespace(claude=SimpleNamespace(as_env=lambda: {}))
    with patch("infrastructure.llm.session._sdk", return_value=(SimpleNamespace, Result, query)), patch("infrastructure.llm.config.load_config", return_value=config):
        result = asyncio.run(_run("inspect", tmp_path, tools=["Read"], allowed_roots=[source], max_turns=3, max_tool_calls=1))
    assert decisions == ["deny", "deny", "allow", "deny"]
    assert captured[0].max_turns == 6  # 3 SDK rounds plus 3 for completion.
    assert result["passed"]


def test_review_defaults_match_loader():
    from agents.review.contract import ReviewConfig, DEFAULTS
    config = ReviewConfig()
    for key, expected in (("max_turns", 36), ("max_tool_calls", 48), ("timeout_seconds", 900)):
        assert getattr(config, key) == DEFAULTS[key] == expected
