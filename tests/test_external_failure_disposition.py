"""External-failure skips survive model parsing and cannot conceal source edits."""
import json
from unittest.mock import Mock, patch

import pytest

from agents.unified_fixer.graph import run_unified_fixer
from agents.unified_fixer.models import UnifiedFixerConfig
from core.task_outcome import task_outcome
from infrastructure.llm.output_schema import output_schema
from workflow.engine import routes


def execute(tmp_path, output, mutate=False):
    carrier = tmp_path / 'gathers/TEST'
    carrier.mkdir(parents=True)
    source = carrier / 'main.py'
    source.write_text('value = 1\n')

    def respond(request):
        if mutate:
            source.write_text('value = 2\n')
        return {'result': json.dumps(output)}

    state = {'run_id': 'r', 'task_id': 't', 'attempt': 1, 'carrier': 'TEST',
             'workspace_path': str(tmp_path),
             'problem': {'carrier': 'TEST', 'service_name': 'gather.py.track',
                         'biztype': 'framework.track_server', 'error': 'connection failed'},
             'evidence': {'framework': 'gathers'}}
    with patch('agents.unified_fixer.graph.create_provider', return_value=Mock(side_effect=respond)):
        return run_unified_fixer(state, UnifiedFixerConfig())


@pytest.mark.parametrize('attribution', ['official', 'environment'])
def test_external_failure_skips_without_entering_delivery(tmp_path, attribution):
    assert 'skip_reason' in output_schema('unified_fixer')['required']
    result = execute(tmp_path, {'status': 'ANALYSIS_ONLY', 'attribution_type': attribution,
                                'skip_reason': '纯外部连接故障，无本地处理缺陷证据'})
    assert result['result'] == 'passed'
    assert not result['passed']
    assert result['changed_files'] == []
    assert result['unified_fixer']['skip_reason']
    assert routes.after_unified_fixer(result) == routes.REVIEW
    assert task_outcome({'status': 'MANUAL', 'steps': [
        {'name': 'unified_fixer_agent', 'output': result['unified_fixer']}]}) == 'FAILED'


@pytest.mark.parametrize('extra,mutate', [
    ({'attribution_type': 'mixed'}, False),
    ({'status': 'FIXED'}, False),
    ({'proposed_edit_files': ['gathers/TEST/main.py']}, False),
    ({'changed_files': ['gathers/TEST/main.py']}, False),
    ({'change_plan': {'scope': 'carrier', 'edits': [{'path': 'gathers/TEST/main.py', 'action': 'update'}]}}, False),
    ({}, True),
])
def test_contradictory_skip_is_failure_not_skipped_delivery(tmp_path, extra, mutate):
    result = execute(tmp_path, {'status': 'ANALYSIS_ONLY', 'attribution_type': 'official',
                                'skip_reason': '外部故障', **extra}, mutate)
    assert result['failure_kind'] == 'invalid_skip_disposition'
    assert result['result'] == 'failed'
    assert not result['skip_reason']
    assert routes.after_unified_fixer(result) == routes.FINALIZE
    assert task_outcome({'status': 'MANUAL', 'steps': [
        {'name': 'unified_fixer_agent', 'output': result['unified_fixer']}]}) == 'FAILED'


def test_proven_local_handling_defect_is_still_reviewed(tmp_path):
    result = execute(tmp_path, {'status': 'FIXED', 'attribution_type': 'mixed',
                                'skip_reason': '', 'changed_files': ['gathers/TEST/main.py']}, True)
    assert result['result'] == 'passed'
    assert result['passed']
    assert routes.after_unified_fixer(result) == routes.REVIEW
