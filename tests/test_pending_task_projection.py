"""完整任务目录只影响视图，不能提前创建任务步骤或生命周期。"""
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from core.contracts import WORKFLOW_VERSION, StepRecord
from infrastructure.storage.lifecycle import LifecycleStore
from infrastructure.storage.store import RecordStore
from server import run_service
from server.routes import common, runs
from server.projections import task_catalog, task_counts
from server.task_board import paginate_tasks


@pytest.mark.parametrize('project', [common._run_payload, run_service._run_payload])
def test_pending_tasks_are_visible_before_execution_and_after_cancellation(tmp_path, project):
    store = RecordStore(tmp_path)
    store.create_run('run', {'workflow_version': WORKFLOW_VERSION})
    tasks = [{'task_id': f'task-{index}', 'carrier': f'C{index}', 'biztype': 'track',
              'problem': {'carrier': f'C{index}', 'biztype': 'track', 'service_name': 'test'}} for index in range(3)]
    store.save('run', StepRecord(name='identify_carrier', title='identify', status='succeeded', output={'tasks': tasks}), '')
    store.save('run', StepRecord(name='prepare_workspace', title='prepare', status='running', input={'carrier': 'C0'}), 'task-0')
    life = LifecycleStore(store.root)
    life.update_run('run', status='running', worker_active=True, heartbeat_at=datetime.now(timezone.utc).isoformat())
    payload = project(store, 'run')
    assert [task['task_id'] for task in payload['tasks']] == ['task-0', 'task-1', 'task-2']
    assert [task['status'] for task in payload['tasks']] == ['RUNNING', 'PENDING', 'PENDING']
    assert payload['tasks'][1]['carrier'] == 'C1'
    assert payload['summary']['tasks_total'] == 3
    assert payload['summary']['tasks_pending'] == 2
    assert payload['summary']['tasks_running'] == 1
    assert payload['dispatched_tasks'] == 1
    assert len(store.steps_snapshot('run')['tasks']) == 1
    assert life.read_task('run', 'task-1') == {}
    life.update_run('run', status='cancelled', worker_active=False)
    cancelled = project(store, 'run')
    assert len(cancelled['tasks']) == 3
    assert cancelled['tasks'][2]['status'] == 'PENDING'
    with patch.object(runs, '_store', return_value=store), patch.object(runs, '_run_payload', return_value=cancelled):
        board = runs.v1_run_board('run', None, page=1, page_size=20, category='pending', keyword='')
    assert board['total'] == 2 and board['all_total'] == 3
    assert board['counts']['pending'] == 2


def test_catalog_reads_full_handoff_and_deduplicates_slots(tmp_path):
    path = tmp_path / 'workflow/outputs/identify_carrier.json'
    path.parent.mkdir(parents=True)
    tasks = [{'task_id': f'task-{index}', 'carrier': f'C{index}'} for index in range(35)]
    path.write_text(json.dumps({'outcome': {'output': {'tasks': tasks}}}), encoding='utf-8')
    catalog, dispatched = task_catalog({'tasks': [{'task_id': 'task-0'}], 'identify_carrier': {'output': {'tasks': tasks[:2]}}}, tmp_path)
    assert len(catalog) == 35 and dispatched == 1
    page = paginate_tasks([{'task_id': row['task_id'], 'result': 'PENDING'} for row in catalog], 2, 20)
    assert page['all_total'] == 35 and len(page['tasks']) == 15
    assert page['counts']['pending'] == 35 and page['counts']['active'] == 0


def test_completed_and_pending_counts_are_mutually_exclusive():
    rows = [{'status': status} for status in ('COMPLETED', 'MANUAL', 'FAILED', 'SKIPPED', 'CANCELLED', 'PENDING', 'RUNNING')]
    rows[0]['report'] = {'delivery_confirmed': True,
                         'delivery': {'commit': 'abc', 'pushed': True, 'analysis_only': True}}
    counts = task_counts(rows, [])
    assert counts['tasks_total'] == 7
    assert counts['tasks_completed'] == 5
    assert counts['tasks_success'] == 1
    assert counts['tasks_unchanged_delivered'] == 1
    assert counts['tasks_manual'] == 0
    assert counts['tasks_pending'] == 1 and counts['tasks_running'] == 1


def test_original_message_comes_from_full_handoff_not_step_preview(tmp_path):
    from server.routes import tasks as routes
    store = RecordStore(tmp_path)
    store.create_run('run', {'workflow_version': WORKFLOW_VERSION})
    message = '  ORIGINAL\n' + 'stack frame\n' * 1000 + '\n END  '
    task = {'task_id': 'pending', 'carrier': 'TEST', 'biztype': 'track',
            'problem': {'carrier': 'TEST', 'biztype': 'track', 'service_name': 'test', 'message': message}}
    store.save('run', StepRecord(name='identify_carrier', title='identify', status='succeeded', output={'tasks': [task]}), '')
    store.write_artifact('run', 'workflow/outputs/identify_carrier.json', {'outcome': {'output': {'tasks': [task]}}})
    payload = common._run_payload(store, 'run')
    assert payload['tasks'][0]['problem']['message'] == message
    with patch.object(routes, '_store', return_value=store), patch.object(routes, '_run_payload', return_value=payload):
        original = routes.v1_task_problem_log('run', 'pending', None)
    assert original['message'] == message
    assert not original['truncated']
