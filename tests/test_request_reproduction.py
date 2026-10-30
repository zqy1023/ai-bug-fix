import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agents.unified_fixer.graph import run_unified_fixer
from agents.unified_fixer.models import UnifiedFixerConfig
from infrastructure.workspace.reproduction import needs_reproduction, original_request, reproduce_before_analysis


def problem():
    model = {"carrier": "TEST", "track_config": {"timeout": 30000},
             "response": "recorded response", "requests": [{"number": "123", "gather_channel": "Default"}]}
    return {"carrier": "TEST", "service_name": "gather.py.track", "biztype": "framework.track_server",
            "message": "解析异常,model:" + json.dumps(model) + ", msg:None", "response": "recorded response"}


def test_tool_blocked_analysis_cannot_be_delivered_as_unchanged(tmp_path):
    from workflow.engine.routes import after_unified_fixer, FINALIZE
    from core.task_outcome import task_outcome

    state = {"run_id": "r", "task_id": "t", "attempt": 1, "carrier": "TEST", "workspace_path": str(tmp_path),
             "problem": problem(), "evidence": {"framework": "gathers"}}
    provider = Mock(return_value={"result": json.dumps({"status": "ANALYSIS_ONLY", "facts": ["root cause found"]}),
                                 "usage": {"tool_stop_kind": "turn_budget_exhausted", "tool_blocked_writes": 1,
                                           "tool_stop_reason": "budget blocked Edit"}})
    with patch("agents.unified_fixer.graph.create_provider", return_value=provider):
        result = run_unified_fixer(state, UnifiedFixerConfig())
    assert result["failure_kind"] == "turn_budget_exhausted"
    assert result["result"] == "failed"
    assert result["facts"] == ["root cause found"]
    assert after_unified_fixer({"unified_fixer": result["unified_fixer"]}) == FINALIZE
    assert task_outcome({"status": "MANUAL", "steps": [{"name": "unified_fixer_agent", "output": result}]}) == "FAILED"


def test_ambiguous_exception_requires_request_even_with_recorded_response():
    value = problem()
    assert needs_reproduction(value)
    request = original_request(value)
    assert request == {"carrier": "TEST", "track_config": {"timeout": 30000},
                       "requests": [{"number": "123", "gather_channel": "Default"}]}
    assert not needs_reproduction({**value, "exception_class": "IndexError"})
    assert not needs_reproduction({"message": "ValueError: invalid value"})
    assert not needs_reproduction({"message": "解析异常 msg: invalid date"})
    assert needs_reproduction({"message": "解析异常 msg:None"})


def test_unavailable_runtime_is_failure_after_model_analysis(tmp_path):
    state = {"run_id": "r", "task_id": "t", "attempt": 1, "carrier": "TEST",
             "workspace_path": str(tmp_path), "problem": problem(), "evidence": {"framework": "gathers"}}
    provider = Mock(return_value={"result": json.dumps({"status": "INSUFFICIENT_EVIDENCE", "facts": ["已检查日志及源码，无法定位"]})})
    with patch("agents.unified_fixer.graph.create_provider", return_value=provider):
        result = run_unified_fixer(state, UnifiedFixerConfig())
    provider.assert_called_once()
    assert result["changed_files"] == []
    assert result["failure_kind"] == "reproduction_unavailable"
    assert not result.get("skip_reason")
    assert (tmp_path / "artifacts/reproduction.json").is_file()


def test_model_timeout_remains_failure_after_provider_parsing_and_projection(tmp_path):
    from agents.unified_fixer.models import UnifiedFixerView
    from core.task_outcome import task_outcome
    from workflow.engine.routes import after_unified_fixer, FINALIZE

    (tmp_path / "gathers/TEST").mkdir(parents=True)
    (tmp_path / "gathers/TEST/main.py").write_text("value=1\n", encoding="utf-8")

    def timeout(**kwargs):
        raise TimeoutError("AGENT_TIMEOUT")

    state = {"run_id": "r", "task_id": "t", "attempt": 1, "carrier": "TEST",
             "workspace_path": str(tmp_path), "problem": {**problem(), "exception_class": "IndexError"},
             "evidence": {"framework": "gathers"}}
    runtime = SimpleNamespace(config=UnifiedFixerConfig(model="test-model", max_provider_attempts=1),
                              agent_runner=timeout)
    with patch("agents.unified_fixer.provider.invoke_with_audit", side_effect=lambda request, call: call()):
        from agents.unified_fixer.graph import build_graph
        result = build_graph(runtime).invoke(state)
    view = UnifiedFixerView.model_validate({key: value for key, value in result.items()
                                           if key in UnifiedFixerView.model_fields}).model_dump()
    assert view['failure_kind'] == 'agent_timeout'
    assert after_unified_fixer({'unified_fixer': view}) == FINALIZE
    assert task_outcome({'status': 'MANUAL', 'steps': [
        {'name': 'unified_fixer_agent', 'output': view}]}) == 'FAILED'


def fake_project(root: Path, *, fail: bool):
    (root / "framework").mkdir(parents=True)
    (root / "gathers/TEST").mkdir(parents=True)
    (root / "framework/__init__.py").write_text("", encoding="utf-8")
    (root / "gathers/TEST/main.py").write_text(
        "def parse():\n    return [][0]\n" if fail else "def parse():\n    return 1\n", encoding="utf-8")
    (root / "framework/models.py").write_text(
        "class TrackModel:\n    @classmethod\n    def model_validate(cls, value):\n        return value\n", encoding="utf-8")
    (root / "framework/track_server.py").write_text(
        "from types import SimpleNamespace\nfrom gathers.TEST.main import parse\n"
        "class TrackServer:\n    async def track(self, model):\n"
        "        assert model['requests'][0]['number'] == '123'\n"
        "        assert model['track_config']['timeout'] == 30000\n"
        "        assert 'response' not in model\n"
        "        error = 'Succeed'\n        try:\n            parse()\n"
        "        except IndexError:\n            error = 'ProcessError'\n"
        "        return SimpleNamespace(items=[SimpleNamespace(error_type=SimpleNamespace(name=error))], "
        "model_dump=lambda **kwargs: {'error': error})\n", encoding="utf-8")


def test_real_subprocess_captures_swallowed_stack_in_isolated_project(tmp_path):
    source = tmp_path / "source"
    fake_project(source, fail=True)
    target = source / "gathers/TEST/main.py"
    target.chmod(0o444)
    try:
        report = reproduce_before_analysis({"problem": problem(), "source_project_path": str(source),
                                             "source_snapshot": {"baseline_dir": str(source)}})
    finally:
        target.chmod(0o600)
    assert report["reproduced"], report
    assert report["exceptions"][0]["exception_class"] == "IndexError"
    assert "return [][0]" in report["exceptions"][0]["traceback"]
    assert not list(source.rglob("__pycache__"))


def test_request_success_is_not_reproduction(tmp_path):
    fake_project(tmp_path, fail=False)
    report = reproduce_before_analysis({"problem": problem(), "source_project_path": str(tmp_path)})
    assert not report["reproduced"]
    assert report["status"] == "not_reproduced"


def test_reproduced_stack_is_handed_to_analysis(tmp_path):
    (tmp_path / "gathers/TEST").mkdir(parents=True)
    (tmp_path / "gathers/TEST/main.py").write_text("value=1\n", encoding="utf-8")
    report = {"reproduced": True, "reason": "reproduced", "exceptions": [
        {"exception_class": "IndexError", "traceback": "STACK_EVIDENCE"}]}
    provider = Mock(side_effect=[{"result": json.dumps({"status": "INSUFFICIENT_EVIDENCE"})},
                                 {"result": json.dumps({"status": "ANALYSIS_ONLY"})}])
    state = {"run_id": "r", "task_id": "t", "attempt": 1, "carrier": "TEST",
             "workspace_path": str(tmp_path), "problem": problem(), "evidence": {"framework": "gathers"}}
    with patch("agents.unified_fixer.graph.reproduce_before_analysis", return_value=report), \
         patch("agents.unified_fixer.graph.create_provider", return_value=provider):
        result = run_unified_fixer(state, SimpleNamespace(config=UnifiedFixerConfig(model="test-model")))
    assert "STACK_EVIDENCE" in provider.call_args.args[0].prompt
    assert provider.call_count == 2
    assert result["_audit"]["reproduction"] == report


def test_ambiguous_log_can_be_analyzed_and_fixed_without_request(tmp_path):
    target = tmp_path / 'gathers/TEST/main.py'
    target.parent.mkdir(parents=True)
    target.write_text('value = [][0]\n', encoding='utf-8')

    def fix(request):
        target.write_text('value = None\n', encoding='utf-8')
        return {'result': json.dumps({'status': 'FIXED', 'changed_files': ['gathers/TEST/main.py']}),
                'usage': {'tool_stop_kind': 'turn_budget_exhausted', 'tool_blocked_writes': 0}}

    state = {'run_id': 'r', 'task_id': 't', 'attempt': 1, 'carrier': 'TEST', 'workspace_path': str(tmp_path), 'problem': problem(),
             'evidence': {'framework': 'gathers'}}
    with patch('agents.unified_fixer.graph.create_provider', return_value=Mock(side_effect=fix)), \
         patch('agents.unified_fixer.graph.reproduce_before_analysis') as reproduce:
        result = run_unified_fixer(state, UnifiedFixerConfig())
    reproduce.assert_not_called()
    assert result['status'] == 'FIXED'
    assert result['changed_files'] == ['gathers/TEST/main.py']
    assert not result.get('failure_kind')
    assert result['result'] == 'passed'


def test_explicit_error_does_not_require_request_when_analysis_has_gaps(tmp_path):
    state = {'run_id': 'r', 'task_id': 't', 'attempt': 1, 'carrier': 'TEST', 'workspace_path': str(tmp_path),
             'problem': {**problem(), 'message': 'IndexError: list index out of range'},
             'evidence': {'framework': 'gathers'}}
    provider = Mock(return_value={'result': json.dumps({'status': 'INSUFFICIENT_EVIDENCE'})})
    with patch('agents.unified_fixer.graph.create_provider', return_value=provider), \
         patch('agents.unified_fixer.graph.reproduce_before_analysis') as reproduce:
        result = run_unified_fixer(state, UnifiedFixerConfig())
    reproduce.assert_not_called()
    assert not result.get('skip_reason')


def test_unsuccessful_request_preserves_initial_analysis_and_skips(tmp_path):
    state = {'run_id': 'r', 'task_id': 't', 'attempt': 1, 'carrier': 'TEST', 'workspace_path': str(tmp_path), 'problem': problem(),
             'evidence': {'framework': 'gathers'}}
    provider = Mock(return_value={'result': json.dumps({'status': 'INSUFFICIENT_EVIDENCE',
                                                     'facts': ['INITIAL_ANALYSIS']})})
    report = {'reproduced': False, 'status': 'not_reproduced', 'reason': '未复现，证据不足'}
    with patch('agents.unified_fixer.graph.create_provider', return_value=provider), \
         patch('agents.unified_fixer.graph.reproduce_before_analysis', return_value=report):
        result = run_unified_fixer(state, UnifiedFixerConfig())
    assert result['skip_reason'] == report['reason']
    assert result['facts'] == ['INITIAL_ANALYSIS']
    assert result['_audit']['call_count'] == 1
