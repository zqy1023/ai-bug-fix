import tempfile
import unittest

from core.contracts import StepRecord
from infrastructure.storage.lifecycle import LifecycleStore
from infrastructure.storage.store import RecordStore
from server.routes.common import _settle_task_lifecycles_after_run_failure


class WorkerTimeoutSettlementTests(unittest.TestCase):
    def _seed(self, directory: str) -> tuple[RecordStore, LifecycleStore]:
        store = RecordStore(directory)
        store.save('run', StepRecord(
            name='request_approval', title='人工审批', status='waiting', attempt=1,
        ), 'task')
        lifecycle = LifecycleStore(store.root)
        lifecycle.update_task(
            'run', 'task', status='waiting_approval', current_step='request_approval',
            attempt=1, allow_reactivate=True,
        )
        return store, lifecycle

    def test_worker_timeout_closes_waiting_approval_task(self):
        with tempfile.TemporaryDirectory() as directory:
            store, lifecycle = self._seed(directory)
            _settle_task_lifecycles_after_run_failure(
                'run', store, phase='worker_timeout',
                reason='后台工作线程心跳已超时', next_action='重新创建批次',
            )
            state = lifecycle.read_task('run', 'task')
            self.assertEqual(state.get('status'), 'failed')
            step = store.load('run', 'request_approval', 'task')
            self.assertEqual(step.status, 'failed')

    def test_approval_recovery_keeps_waiting_task(self):
        with tempfile.TemporaryDirectory() as directory:
            store, lifecycle = self._seed(directory)
            _settle_task_lifecycles_after_run_failure(
                'run', store, phase='approval_recovery',
                reason='审批恢复失败', next_action='重新提交审批',
            )
            state = lifecycle.read_task('run', 'task')
            self.assertEqual(state.get('status'), 'waiting_approval')


if __name__ == '__main__':
    unittest.main()
