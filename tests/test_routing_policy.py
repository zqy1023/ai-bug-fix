"""Disposition, review, retry, delivery and budget boundaries work together."""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from agents.review.contract import ReviewConfig, ReviewConfigError, delivery_allowed, review_budget
from agents.review.graph import review
from core.task_outcome import task_outcome
from infrastructure.llm.tool_budget import ToolBudget
from workflow.engine import routes


def approval():
    return {'verdict': 'PASS', 'passed': True, 'code_verdict': 'PASS',
            'coverage': {'decision': 'sufficient', 'reason': 'source and raw input checked',
                         'reviewed_symbols': ['parse'], 'missing_checks': []},
            'dimensions': {key: {'status': 'passed', 'reason': 'source and raw input checked',
                                 'evidence': ['main.py:1']} for key in ['syntax', 'business', 'scope']},
            'summary': 'independent review', 'findings': [], 'required_changes': [], 'todo': []}


def skip_state():
    return {'last_result': 'passed', 'unified_fixer': {'status': 'ANALYSIS_ONLY',
             'attribution_type': 'official', 'skip_reason': 'external failure'}, 'agent_policy': {'max_repairs': 2}}


def test_model_skip_must_be_reviewed_and_review_can_return_it_to_repair():
    state = skip_state()
    assert routes.after_unified_fixer(state) == routes.REVIEW
    state['review'] = approval()
    assert routes.after_review(state) == routes.FINALIZE
    records = [{'name': 'unified_fixer_agent', 'output': state['unified_fixer']},
               {'name': 'review_agent', 'output': state['review']}]
    assert task_outcome({'status': 'MANUAL', 'steps': records}) == 'SKIPPED'
    state['review'] = {'verdict': 'FAIL', 'code_verdict': 'FAIL'}
    assert routes.after_review(state) == routes.RETRY
    records[-1]['output'] = state['review']
    assert task_outcome({'status': 'MANUAL', 'steps': records}) == 'FAILED'
    state['automatic_attempt'] = 3
    assert routes.after_review(state) == routes.FINALIZE


def test_uncertain_skip_does_not_become_skipped_or_delivered():
    state = skip_state()
    state['review'] = {'verdict': 'MANUAL', 'code_verdict': 'UNKNOWN'}
    assert routes.after_review(state) == routes.FINALIZE
    assert task_outcome({'status': 'MANUAL', 'report': {'review': state['review'], 'skip_reason': 'external failure'}}) == 'FAILED'


@pytest.mark.parametrize('limit', [1, 3, 10])
def test_repair_budget_counts_repairs_after_initial_attempt(limit):
    state = {'review': {'verdict': 'FAIL', 'code_verdict': 'FAIL'}, 'last_result': 'failed',
             'agent_policy': {'automatic_repair': True, 'max_repairs': limit}, 'max_attempts': 1}
    for attempt in range(1, limit + 1):
        assert routes.after_review({**state, 'automatic_attempt': attempt}) == routes.RETRY
    assert routes.after_review({**state, 'automatic_attempt': limit + 1}) == routes.FINALIZE


def test_error_report_delivery_does_not_hide_rejected_patch():
    report = {'review': {'code_verdict': 'FAIL', 'verdict': 'FAIL'}, 'delivery_confirmed': True,
              'delivery': {'commit': 'abc', 'pushed': True, 'analysis_only': True}}
    assert task_outcome({'status': 'UNCHANGED_DELIVERED', 'report': report}) == 'FAILED'


def test_recovered_attempt_is_not_poisoned_by_old_failure():
    assert task_outcome({'status': 'CHANGED_DELIVERED', 'steps': [
        {'name': 'review_agent', 'output': {'code_verdict': 'FAIL', 'verdict': 'FAIL'}},
        {'name': 'review_agent', 'output': approval()}]}) == 'CHANGED_DELIVERED'


def test_semantic_proof_and_runtime_coverage_have_different_delivery_rules():
    result = approval()
    result.update(verdict='MANUAL', passed=False)
    result['dimensions']['business']['status'] = 'unverified'
    assert not delivery_allowed(result)
    assert routes.after_review({'review': result}) == routes.FINALIZE
    result['semantic_verified'] = True
    assert delivery_allowed(result)
    assert routes.after_review({'review': result}) == routes.REPORT


def test_budget_tail_allows_only_modified_file_verification(tmp_path):
    budget = ToolBudget(4, writable=True)
    for index in range(2):
        assert budget.decide('Read', {'file_path': str(tmp_path / str(index))}, 'allow', '')[0] == 'allow'
    assert budget.decide('Read', {'file_path': str(tmp_path / 'new')}, 'allow', '')[0] == 'deny'
    target = {'file_path': str(tmp_path / 'main.py')}
    assert budget.decide('Edit', target, 'allow', '')[0] == 'allow'
    assert budget.decide('Read', {'file_path': str(tmp_path / 'other')}, 'allow', '')[0] == 'deny'
    assert budget.decide('Read', target, 'allow', '')[0] == 'allow'
    assert budget.decide('Read', target, 'allow', '')[0] == 'deny'


def test_review_resources_scale_without_overriding_user_caps():
    config = ReviewConfig()
    small = review_budget(config, ['main.py'], 100)
    risk = review_budget(config, ['main.py'], 100, True)
    assert small['max_tool_calls'] < risk['max_tool_calls']
    assert small['max_context_chars'] < risk['max_context_chars']
    assert review_budget(ReviewConfig(max_tool_calls=5), ['main.py'], 100, True)['max_tool_calls'] == 5


def test_disabled_review_never_invokes_model_or_approves_delivery(tmp_path):
    (tmp_path / 'gathers/TEST').mkdir(parents=True)
    runtime = SimpleNamespace(config=ReviewConfig(enabled=False), run=Mock())
    result = review({'ai_workspace_path': str(tmp_path), 'carrier': 'TEST', 'framework': 'gathers'}, runtime)
    runtime.run.assert_not_called()
    assert result['contract_error'] == 'review_disabled'
    assert routes.after_review({'review': result}) == routes.FINALIZE


def test_acceptance_switch_really_disables_execution(tmp_path):
    from infrastructure.workspace.validation import run_validation
    with patch('infrastructure.workspace.validation.run_fixed_commands') as commands:
        result = run_validation({'ai_workspace_path': str(tmp_path), 'agent_policy': {'acceptance': False}})
    commands.assert_not_called()
    assert result['executions'] == []
    assert result['replay']['status'] == 'unverified'


def test_invalid_explicit_config_fails_instead_of_falling_back(tmp_path):
    config = tmp_path / 'review.json'
    config.write_text('{invalid')
    with pytest.raises(ReviewConfigError):
        ReviewConfig.load(config, env={})
    config.write_text(json.dumps({'max_tool_calls': 0}))
    with pytest.raises(ReviewConfigError):
        ReviewConfig.load(config, env={})


def test_legacy_analysis_switch_stops_before_model_invocation(tmp_path):
    from agents.unified_fixer.graph import run_unified_fixer
    with patch('agents.unified_fixer.graph.create_provider') as provider:
        result = run_unified_fixer({'workspace_path': str(tmp_path), 'carrier': 'TEST',
                'evidence': {'framework': 'spiders'}, 'agent_policy': {'analyze_legacy': False}}, None)
    provider.assert_not_called()
    assert routes.after_unified_fixer(result) == routes.FINALIZE
    assert result['skip_reason'] == '策略关闭旧框架分析'


@pytest.mark.parametrize('mode', ['approved_patch', 'approved_skip', 'rejected_patch'])
def test_compiled_task_flow_never_publishes_rejected_or_skipped_work(mode):
    from workflow.engine.graph import build_task_graph
    calls = []

    def node(name):
        def run(state):
            calls.append(name)
            if name == routes.UNIFIED_FIXER:
                output = skip_state()['unified_fixer'] if mode == 'approved_skip' else {'status': 'FIXED'}
                return {'unified_fixer': output, 'last_result': 'passed'}
            if name == routes.REVIEW:
                result = approval() if mode != 'rejected_patch' else {'verdict': 'FAIL', 'code_verdict': 'FAIL'}
                return {'review': result, 'last_result': 'passed' if mode != 'rejected_patch' else 'failed',
                        'retry_target': routes.UNIFIED_FIXER}
            return {'last_result': 'passed'}
        return run

    manager = SimpleNamespace(reset_task_workspace=Mock())
    graph = build_task_graph({name: node(name) for name in routes.TASK_NODE_NAMES if name != routes.RETRY},
                             workspace_manager=manager, retry_context_builder=lambda state: {},
                             entry_node=routes.UNIFIED_FIXER)
    graph.invoke({'run_id': 'r', 'task_id': 't', 'task': {'carrier': 'TEST'}, 'attempt': 1,
                  'automatic_attempt': 1, 'agent_policy': {'max_repairs': 2}}, {'recursion_limit': 50})
    assert calls[-1] == routes.FINALIZE
    if mode == 'approved_patch':
        assert routes.REPORT in calls and routes.SUBMIT in calls
    else:
        assert routes.REPORT not in calls and routes.SUBMIT not in calls
    assert calls.count(routes.UNIFIED_FIXER) == (3 if mode == 'rejected_patch' else 1)
    assert calls.count(routes.REVIEW) == (3 if mode == 'rejected_patch' else 1)
