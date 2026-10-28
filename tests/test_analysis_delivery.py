import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from core.errors import StepFailed
from infrastructure.storage.store import RecordStore
from workflow.stages.generate_report.models import DeliveryInput
from workflow.stages.generate_report.node import run

class AnalysisDeliveryTests(unittest.TestCase):
    def test_prepare_then_publish_without_code_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            delivery = Mock()
            delivery.deliver.return_value = {'commit': 'abc', 'local_branch': True}
            ctx = SimpleNamespace(store=RecordStore(directory), run=SimpleNamespace(run_id='run'), task=SimpleNamespace(biztype='track'), services=SimpleNamespace(delivery=delivery))
            data = DeliveryInput(task_id='task', carrier='TEST', repair_branch='repair-test', ai_workspace_path=directory, changed_files=[], analysis_only=True, report_paths=['gathers/TEST/final.json'], allow_push=True, require_push=True)
            with patch('workflow.stages.finalize.node.build_report', return_value={'result': 'MANUAL'}):
                prepared = run(ctx, data)
                self.assertFalse(delivery.deliver.call_args.args[0]['allow_push'])
                self.assertFalse(delivery.deliver.call_args.args[0]['require_push'])
                published = run(ctx, data.model_copy(update={'publish': True, 'delivery_candidate_id': prepared.candidate_id}))
                self.assertEqual(prepared.candidate_id, published.candidate_id)
                self.assertTrue(delivery.deliver.call_args.args[0]['allow_push'])
                with self.assertRaises(StepFailed):
                    run(ctx, data.model_copy(update={'publish': True, 'delivery_candidate_id': 'stale'}))
                delivery.deliver.side_effect = RuntimeError('push failed')
                with self.assertRaises(StepFailed):
                    run(ctx, data.model_copy(update={'publish': True, 'delivery_candidate_id': prepared.candidate_id}))
