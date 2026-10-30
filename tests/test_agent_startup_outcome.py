import pytest

from infrastructure.llm.session import ClaudeAgentUnavailable, validate_process_runtime
from workflow.engine.handoff import outcome_of


@pytest.mark.parametrize("kind", ["agent_startup_failed", "agent_invocation_failed"])
def test_caught_sdk_failure_is_execution_failure(kind):
    result = outcome_of({"status": "MANUAL", "failure_kind": kind,
                         "blocked_reason": "SDK cannot start"}, business="MANUAL")
    assert result.execution.status == "failed"
    assert result.execution.error.message == "SDK cannot start"
    assert result.execution.error.retryable
    assert result.assessment.outcome == "inconclusive"


def test_unknown_business_is_not_execution_failure():
    result = outcome_of({"status": "MANUAL"}, business="MANUAL")
    assert result.execution.status == "succeeded"
    assert result.assessment.outcome == "inconclusive"


def test_runtime_guard_reports_incompatible_import(monkeypatch):
    import anyio
    monkeypatch.setattr(anyio, "open_process", lambda command: None)
    with pytest.raises(ClaudeAgentUnavailable, match="open_process 缺少 user 参数"):
        validate_process_runtime()


def test_project_runtime_supports_sdk_spawn():
    validate_process_runtime()
