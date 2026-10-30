"""交付事实、候选、审查结论和可恢复执行状态不能混为一类。"""
import pytest

from core.task_outcome import task_outcome
from server.projections import task_counts
from server.task_board import paginate_tasks


def delivered(analysis_only=False, review="PASS"):
    return {"status": "MANUAL", "report": {"changed_files": ["gathers/T/main.py"],
            "review": {"code_verdict": review}}, "steps": [
        {"name": "submit_changes", "status": "succeeded", "input": {"analysis_only": analysis_only},
         "output": {"commit": "abc", "pushed": True}},
    ]}


def test_review_unverified_is_not_a_manual_task_terminal():
    task = delivered()
    task['report']['review']['verdict'] = 'MANUAL'
    assert task_outcome(task) == 'CHANGED_DELIVERED'
    task['steps'][0]['input']['analysis_only'] = True
    assert task_outcome(task) == 'UNCHANGED_DELIVERED'


@pytest.mark.parametrize('status', ['MANUAL', 'INSUFFICIENT_EVIDENCE', 'BLOCKED', 'CANCELLED'])
def test_non_delivery_terminal_preserves_skip_reason(status):
    assert task_outcome({'status': status, 'report': {'terminal_reason': '明确原因'}}) == 'SKIPPED'


def test_prepared_commit_and_failed_publish_do_not_claim_delivery():
    candidate = {'name': 'generate_report', 'status': 'succeeded',
                 'output': {'commit': 'abc', 'local_branch': True}}
    assert task_outcome({'status': 'MANUAL', 'steps': [candidate]}) == 'FAILED'
    task = delivered()
    task['steps'][0]['status'] = 'failed'
    assert task_outcome(task) == 'FAILED'
    assert task_outcome({'status': 'SUCCESS'}) == 'FAILED'


def test_rejected_code_must_not_be_counted_as_source_delivery():
    assert task_outcome(delivered(review='FAIL')) == 'FAILED'
    assert task_outcome(delivered(review='UNKNOWN')) == 'FAILED'
    assert task_outcome(delivered(analysis_only=True, review='FAIL')) == 'FAILED'


def test_model_invocation_failure_is_failure_even_when_diagnostic_report_is_delivered():
    task = delivered(analysis_only=True)
    task['steps'].insert(0, {'name': 'unified_fixer_agent', 'status': 'succeeded',
                            'output': {'failure_kind': 'agent_startup_failed', 'result': 'failed'}})
    assert task_outcome(task) == 'FAILED'


def test_unreproducible_preflight_is_skipped_and_does_not_publish_empty_report():
    from workflow.engine.routes import after_unified_fixer, FINALIZE
    result = {'skip_reason': '无法复现', 'status': 'ANALYSIS_ONLY'}
    assert after_unified_fixer({'unified_fixer': result}) == FINALIZE
    assert task_outcome({'status': 'MANUAL', 'steps': [
        {'name': 'unified_fixer_agent', 'output': result}]}) == 'SKIPPED'
    assert task_outcome({'status': 'MANUAL', 'report': {'skip_reason': '无法复现'}}) == 'SKIPPED'


def test_agent_graph_does_not_drop_platform_skip_reason(tmp_path):
    import json
    from unittest.mock import Mock, patch
    from agents.unified_fixer.graph import build_graph
    from workflow.engine.routes import after_unified_fixer, FINALIZE
    provider = Mock(return_value={'result': json.dumps({'status': 'INSUFFICIENT_EVIDENCE'})})
    with patch('agents.unified_fixer.graph.create_provider', return_value=provider):
        result = build_graph().invoke({'workspace_path': str(tmp_path), 'run_id': 'audit', 'task_id': 'skip',
                                       'attempt': 1, 'carrier': 'TEST',
                                       'problem': {'carrier': 'TEST', 'service_name': 'gather.py.track',
                                                   'biztype': 'framework.track_server', 'message': '解析异常 msg:None'},
                                       'evidence': {'framework': 'gathers'}})
    provider.assert_called_once()
    assert result['skip_reason']
    assert after_unified_fixer(result) == FINALIZE


def test_invocation_failure_routes_to_failure_without_publishing_empty_report():
    from workflow.engine.routes import after_unified_fixer, FINALIZE
    assert after_unified_fixer({'unified_fixer': {'failure_kind': 'agent_timeout'}}) == FINALIZE
    assert task_outcome({'status': 'MANUAL', 'report': {'repair_failure_kind': 'agent_timeout'}}) == 'FAILED'


def test_session_startup_failure_returns_cause_without_waiting_for_timeout(tmp_path, monkeypatch):
    from infrastructure.llm.session import ClaudeSession
    import time

    async def fail_connect(self):
        raise TypeError('SDK process initialization failed')

    monkeypatch.setattr(ClaudeSession, '_serve', fail_connect)
    started = time.monotonic()
    session = ClaudeSession(tmp_path, {}, '', timeout_seconds=30)
    try:
        result = session.invoke('test', tmp_path)
        assert result['failure_kind'] == 'agent_startup_failed'
        assert 'SDK process initialization failed' in result['failure_detail']
        assert time.monotonic() - started < 3
    finally:
        session.close()


def test_retry_and_pause_override_previous_report_without_hiding_pending_tasks():
    task = delivered()
    task['lifecycle'] = {'status': 'running'}
    assert task_outcome(task) == 'RUNNING'
    task['lifecycle']['status'] = 'paused'
    assert task_outcome(task) == 'PAUSED'
    assert task_outcome({'status': 'PENDING'}) == 'PENDING'


def test_board_counts_filters_and_summary_use_same_mutually_exclusive_outcomes():
    tasks = [delivered(), delivered(True), {'status': 'MANUAL'}, {'status': 'FAILED'}, {'status': 'PENDING'}]
    for index, task in enumerate(tasks):
        task['task_id'] = str(index)
    summary = task_counts(tasks, [])
    board = paginate_tasks(tasks, 1, 20)
    assert board['counts'] == {'pending': 1, 'active': 0, 'paused': 0, 'changed_delivered': 1,
                               'unchanged_delivered': 1, 'failed': 1, 'skipped': 1}
    assert summary['tasks_completed'] == 4 and summary['tasks_success'] == 2
    assert [task['result'] for task in board['tasks']] == [
        'CHANGED_DELIVERED', 'UNCHANGED_DELIVERED', 'SKIPPED', 'FAILED', 'PENDING']
    assert paginate_tasks(tasks, 1, 20, 'changed_delivered')['total'] == 1


def test_run_finalization_keeps_full_review_delivery_and_original_problem(tmp_path):
    from core.contracts import StepRecord
    from infrastructure.storage.store import RecordStore
    from workflow.engine.graph import _ensure_task_final_report

    store = RecordStore(tmp_path)
    store.create_run('audit', {})
    store.save('audit', StepRecord(name='finalize_execution', title='收尾', status='succeeded',
        output={'delivery_status': 'UNCHANGED_DELIVERED', 'terminal_reason': '只交报告'}), 'task')
    existing = {'result': 'UNCHANGED_DELIVERED', 'review': {'code_verdict': 'UNKNOWN'},
                'delivery': {'commit': 'abc', 'pushed': True, 'analysis_only': True},
                'problem': {'message': '完整原始异常'}, 'source_evidence': [{'path': 'main.py'}],
                'validation': {'executions': []}}
    report = _ensure_task_final_report(store, 'audit', {}, 'task', existing, finished_at='2026-10-29')
    assert report['result'] == 'FAILED'
    for key in ('review', 'delivery', 'problem', 'source_evidence', 'validation'):
        assert report[key] == existing[key]
