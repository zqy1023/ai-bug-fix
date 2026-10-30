"""Skip proposals, unresolved defects and runtime verification stay distinct."""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from agents.review import SPEC
from agents.review.contract import delivery_allowed, judge
from agents.review.graph import review
from agents.unified_fixer.graph import run_unified_fixer
from agents.unified_fixer.models import UnifiedFixerConfig
from core.task_outcome import task_outcome
from infrastructure.storage.store import RecordStore
from workflow.engine import routes
from workflow.engine.graph import build_agent_node


def approval() -> dict:
    return {
        'verdict': 'PASS', 'passed': True, 'code_verdict': 'PASS',
        'coverage': {'decision': 'sufficient', 'reason': 'checked original input and source',
                     'reviewed_symbols': ['gathers/TEST/main.py'], 'missing_checks': []},
        'dimensions': {key: {'status': 'passed', 'reason': 'checked the decisive branch',
                             'evidence': ['gathers/TEST/main.py:1']}
                       for key in ('syntax', 'business', 'scope')},
        'summary': 'approve the decision, without claiming a repair',
        'findings': [], 'required_changes': [], 'todo': [], 'unresolved_issues': [],
    }


@pytest.mark.parametrize('status', ['SUCCESS', 'SKIPPED', 'CANCELLED'])
@pytest.mark.parametrize('review_output', [
    {'verdict': 'FAIL', 'code_verdict': 'FAIL'},
    {'verdict': 'MANUAL', 'code_verdict': 'UNKNOWN'},
    {'contract_error': 'review_invocation_failed:provider_error'},
])
def test_skip_terminal_cannot_hide_review_rejection(status: str, review_output: dict) -> None:
    assert task_outcome({'status': status, 'report': {
        'skip_reason': 'external failure', 'review': review_output}}) == 'FAILED'


@pytest.mark.parametrize('review_output', [{}, {**approval(), 'verdict': 'MANUAL', 'passed': False}])
def test_model_skip_requires_full_independent_approval(review_output: dict) -> None:
    assert task_outcome({'status': 'SKIPPED', 'report': {
        'locate_status': 'ANALYSIS_ONLY', 'attribution_type': 'official',
        'skip_reason': 'external failure', 'review': review_output}}) == 'FAILED'


def test_platform_exclusion_still_needs_no_model_review() -> None:
    assert task_outcome({'status': 'SKIPPED', 'report': {
        'skip_reason': '策略关闭旧框架分析'}}) == 'SKIPPED'


def test_failed_review_step_without_output_is_not_hidden_by_skip() -> None:
    assert task_outcome({'status': 'SKIPPED', 'steps': [
        {'name': 'review_agent', 'status': 'failed', 'output': {}}]}) == 'FAILED'


def test_skip_handoff_and_compiled_review_keep_the_exact_reason(tmp_path) -> None:
    root = tmp_path / 'workspace'
    target = root / 'gathers/TEST/main.py'
    target.parent.mkdir(parents=True)
    target.write_text('value = 1\n', encoding='utf-8')
    reason = 'connection failed before a response, confirmed against the request code'
    requests = []

    def invoke(request):
        requests.append(request)
        return {'result': json.dumps(approval())}

    state = {
        'run_id': 'r', 'task_id': 't', 'attempt': 1,
        'task': {'carrier': 'TEST', 'biztype': 'track', 'service_name': 'test'},
        'framework': 'gathers', 'ai_workspace_path': str(root), 'changed_files': [],
        'problem': {'carrier': 'TEST', 'service_name': 'test', 'biztype': 'track'},
        'unified_fixer': {'status': 'ANALYSIS_ONLY', 'attribution_type': 'official',
                          'skip_reason': reason},
    }
    result = build_agent_node(SPEC, RecordStore(tmp_path / 'records'),
                              runtime=SimpleNamespace(run=invoke))(state)
    assert reason in requests[0].prompt
    assert 'source_ownership' in requests[0].prompt
    saved = result['review']
    assert saved['review_kind'] == 'analysis_disposition'
    assert saved['disposition_verified'] is True
    assert saved['semantic_verified'] is False
    assert saved['validation']['replay']['status'] == 'unverified'
    assert delivery_allowed(saved)
    assert task_outcome({'status': 'SKIPPED', 'steps': [
        {'name': 'unified_fixer_agent', 'output': state['unified_fixer']},
        {'name': 'review_agent', 'output': saved}]}) == 'SKIPPED'


def test_readonly_analysis_can_deliver_with_explicit_unresolved_defect(tmp_path) -> None:
    source = tmp_path / 'spiders/TEST/main.py'
    source.parent.mkdir(parents=True)
    source.write_text('value = mapping[key]\n', encoding='utf-8')
    payload = approval()
    payload['unresolved_issues'] = ['spiders/TEST/main.py:1 has an unmapped key; source is readonly']
    result = review({'run_id': 'r', 'task_id': 't', 'carrier': 'TEST', 'framework': 'spiders',
                     'ai_workspace_path': str(tmp_path), 'changed_files': [], 'attempt': 1,
                     'problem': {'service_name': 'test', 'biztype': 'track', 'carrier': 'TEST'}},
                    SimpleNamespace(run=lambda request: {'result': json.dumps(payload)}))
    assert result['unresolved_issues'] == payload['unresolved_issues']
    assert result['todo'] == []
    assert result['disposition_verified'] is True
    assert result['semantic_verified'] is False
    assert delivery_allowed(result)
    report = {'review': result, 'delivery_confirmed': True,
              'delivery': {'commit': 'abc', 'pushed': True, 'analysis_only': True}}
    assert task_outcome({'status': 'MANUAL', 'report': report}) == 'UNCHANGED_DELIVERED'


def test_patch_cannot_deliver_by_hiding_a_defect_in_unresolved_issues() -> None:
    payload = {**approval(), 'review_kind': 'patch', 'unresolved_issues': ['normal input loses events']}
    assert not delivery_allowed(payload)
    assert routes.after_review({'review': payload}) == routes.FINALIZE
    assert task_outcome({'status': 'CHANGED_DELIVERED', 'report': {'review': payload}}) == 'FAILED'


def test_uncertain_disposition_is_not_deliverable_even_if_code_passes() -> None:
    payload = {**approval(), 'review_kind': 'analysis_disposition', 'disposition_verified': False,
               'verdict': 'MANUAL', 'passed': False}
    assert judge(payload).valid
    assert not delivery_allowed(payload)


def test_missing_response_does_not_block_proven_local_request_fix(tmp_path) -> None:
    target = tmp_path / 'gathers/TEST/main.py'
    target.parent.mkdir(parents=True)
    before = 'def build_request(config):\n    return {"timeout": timeout_ms}\n'
    after = 'def build_request(config):\n    return {"timeout": config["timeout"]}\n'
    target.write_text(before, encoding='utf-8')
    scope = {}
    exec(compile(before, str(target), 'exec'), scope)
    with pytest.raises(NameError):
        scope['build_request']({'timeout': 30000})

    def respond(request):
        assert request.allowed_write_roots
        target.write_text(after, encoding='utf-8')
        return {'result': json.dumps({'status': 'FIXED', 'attribution_type': 'code',
                                     'changed_files': ['gathers/TEST/main.py'], 'skip_reason': '',
                                     'root_cause': 'undefined local before request dispatch'})}

    with patch('agents.unified_fixer.graph.create_provider', return_value=Mock(side_effect=respond)), \
         patch('agents.unified_fixer.graph.reproduce_before_analysis') as reproduce:
        result = run_unified_fixer({
            'run_id': 'r', 'task_id': 't', 'attempt': 1, 'carrier': 'TEST',
            'workspace_path': str(tmp_path), 'evidence': {'framework': 'gathers'},
            'problem': {'carrier': 'TEST', 'service_name': 'test', 'biztype': 'track',
                        'response': '', 'exception_class': 'NameError',
                        'frame': 'main.py:2:build_request', 'track_config': {'timeout': 30000}},
        }, UnifiedFixerConfig())
    reproduce.assert_not_called()
    assert result['passed'] is True
    assert result['changed_files'] == ['gathers/TEST/main.py']
    assert routes.after_unified_fixer(result) == routes.REVIEW
    exec(compile(target.read_text(encoding='utf-8'), str(target), 'exec'), scope)
    assert scope['build_request']({'timeout': 30000}) == {'timeout': 30000}
