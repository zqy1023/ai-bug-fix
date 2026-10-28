import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from core.errors import StepFailed
from infrastructure.storage.store import RecordStore
from workflow.stages.request_approval.models import ApprovalInput, ApprovalOutput
from workflow.stages.request_approval.node import run
from workflow.engine.routes import after_approval, FINALIZE

class ApprovalRecoveryTests(unittest.TestCase):
    def test_reloaded_approval_checks_fingerprint_and_cleanup_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = SimpleNamespace(store=RecordStore(directory), run=SimpleNamespace(run_id='run'), services=SimpleNamespace(delivery=Mock()))
            data = ApprovalInput(candidate_id='candidate', task_id='task', carrier='TEST', ai_workspace_path=directory, baseline_dir=directory, changed_files=[], approval_fingerprint='current', delivery_local_branch=True, repair_branch='repair')
            with patch('workflow.stages.request_approval.node.interrupt', return_value={'decision': 'APPROVE', 'approval_fingerprint': 'old'}):
                with self.assertRaises(StepFailed):
                    run(ctx, data)
            ctx.services.delivery.discard_branch.assert_not_called()
            with patch('workflow.stages.request_approval.node.interrupt', return_value={'decision': 'APPROVE', 'approval_fingerprint': 'current'}), patch('workflow.stages.request_approval.node.final_gate', return_value={'passed': True}):
                self.assertTrue(run(ctx, data).passed)
            ctx.services.delivery.discard_branch.side_effect = RuntimeError('cleanup failed')
            with patch('workflow.stages.request_approval.node.interrupt', return_value={'decision': 'REJECT', 'approval_fingerprint': 'current', 'retry_target': 'unified_fixer_agent'}):
                with self.assertRaises(StepFailed) as caught:
                    run(ctx, data)
            output = ApprovalOutput.model_validate(caught.exception.output).model_dump()
            self.assertTrue(output['approval_cleanup_failed'])
            self.assertEqual(after_approval(output), FINALIZE)
