import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from core.errors import StepFailed
from infrastructure.storage.store import RecordStore
from workflow.stages.request_approval.models import ApprovalInput, ApprovalOutput
from workflow.stages.request_approval.node import run
from workflow.engine.routes import after_approval, FINALIZE
from workflow.stages.request_approval import _input

class ApprovalRecoveryTests(unittest.TestCase):
    def approval_context(self, directory, *, analysis_only=False):
        root = Path(directory)
        baseline = root / 'baseline'
        baseline.mkdir()
        target = root / 'gathers/TEST/main.py'
        target.parent.mkdir(parents=True)
        target.write_text('value = 1\n', encoding='utf-8')
        ctx = SimpleNamespace(store=RecordStore(root / 'runtime'),
                              run=SimpleNamespace(run_id='run'),
                              services=SimpleNamespace(delivery=Mock()))
        state = {'run_id': 'run', 'task_id': 'task', 'task': {'carrier': 'TEST'},
                 'ai_workspace_path': directory, 'baseline_dir': str(baseline),
                 'changed_files': [] if analysis_only else ['gathers/TEST/main.py'],
                 'delivery': {'candidate_id': 'candidate', 'commit': 'abc', 'analysis_only': analysis_only},
                 'delivery_pushed': True, 'repair_branch': 'repair-test'}
        return ctx, ApprovalInput.model_validate(_input(state)), target

    def test_approve_published_code_and_analysis_report_with_real_gate(self):
        for analysis_only in (False, True):
            with self.subTest(analysis_only=analysis_only), tempfile.TemporaryDirectory() as directory:
                ctx, data, _ = self.approval_context(directory, analysis_only=analysis_only)
                with patch('workflow.stages.request_approval.node.interrupt', return_value={
                    'decision': 'APPROVE', 'approval_fingerprint': data.approval_fingerprint,
                }):
                    result = run(ctx, data)
                self.assertTrue(result.passed)
                self.assertEqual(result.candidate_id, data.candidate_id)
                ctx.services.delivery.discard_branch.assert_not_called()

    def test_changed_code_cannot_reuse_approval_version(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx, data, target = self.approval_context(directory)
            target.write_text('value = 2\n', encoding='utf-8')
            with patch('workflow.stages.request_approval.node.interrupt', return_value={
                'decision': 'APPROVE', 'approval_fingerprint': data.approval_fingerprint,
            }), self.assertRaises(StepFailed) as caught:
                run(ctx, data)
            self.assertIn('源码变化', str(caught.exception))

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
