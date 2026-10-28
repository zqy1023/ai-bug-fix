import tempfile
import unittest
from unittest.mock import patch
from agents.unified_fixer import SPEC
from infrastructure.storage.store import RecordStore
from infrastructure.storage.lifecycle import LifecycleStore
from workflow.engine.runner import StepRunner, StepRunRequest

class StepRetryLifecycleTests(unittest.TestCase):
    def test_retry_reactivates_failed_run_and_task(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RecordStore(directory)
            lifecycle = LifecycleStore(directory)
            lifecycle.update_run('run', status='failed')
            lifecycle.update_task('run', 'task', status='failed')
            request = StepRunRequest(run_id='run', task_id='task', step_name=SPEC.name)
            with patch('workflow.engine.runner._archive_step_retry'):
                StepRunner(store)._mark_running(SPEC, request)
            self.assertEqual(lifecycle.read_run('run')['status'], 'running')
            self.assertEqual(lifecycle.read_task('run', 'task')['status'], 'running')
            self.assertEqual(store.load('run', SPEC.name, 'task').status, 'running')

    def test_single_step_completion_settles_both_lifecycles(self):
        from core.contracts import StepRecord, StepError
        for code, expected in [(0, 'paused'), (1, 'failed')]:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                store = RecordStore(directory)
                lifecycle = LifecycleStore(directory)
                lifecycle.update_run('run', status='running')
                lifecycle.update_task('run', 'task', status='running')
                record = StepRecord(name=SPEC.name, title='test', status='succeeded' if code == 0 else 'failed', error=None if code == 0 else StepError(code='STEP_EXECUTION_FAILED', message='failed'))
                request = StepRunRequest(run_id='run', task_id='task', step_name=SPEC.name)
                with patch('workflow.engine.runner.execute_once', return_value=(code, record, {})):
                    StepRunner(store)._execute(SPEC, request)
                self.assertEqual(lifecycle.read_run('run')['status'], expected)
                self.assertEqual(lifecycle.read_task('run', 'task')['status'], expected)
