import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from core.contracts import StepError, StepRecord
from infrastructure.storage.store import RecordStore
from workflow.stages.finalize.models import FinalizeInput
from workflow.stages.finalize.node import build_report, run
from workflow.stages.generate_report.models import DeliveryInput
from workflow.stages.generate_report.node import run as deliver


class FinalizeReportingTests(unittest.TestCase):
    def context(self, directory):
        store = RecordStore(directory)
        store.save('run', StepRecord(
            name='unified_fixer_agent', title='统一分析修复模型', status='succeeded',
            result='MANUAL', output={
                'status': 'ANALYSIS_ONLY', 'root_cause': '真实分析结论',
                'checks': {'source_read': True}, 'agent_policy': {'scope': 'carrier'},
                'acceptance_criteria': ['保留原业务分类'],
            }), 'task')
        return SimpleNamespace(store=store, run=SimpleNamespace(run_id='run'),
                               task=SimpleNamespace(biztype='track'),
                               services=SimpleNamespace(delivery=Mock()))

    def test_report_reads_agent_output_and_keeps_original_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(directory)
            ctx.store.save('run', StepRecord(
                name='prepare_evidence', title='准备证据', status='failed',
                error=StepError(code='STEP_EXECUTION_FAILED', message='缺少原始证据'),
            ), 'task')
            report = build_report(ctx, FinalizeInput(
                task_id='task', carrier='TEST', biztype='track', terminal_status='failed'))
            self.assertEqual(report['root_cause'], '真实分析结论')
            self.assertEqual(report['analysis_checks'], {'source_read': True})
            self.assertEqual(report['agent_policy'], {'scope': 'carrier'})
            self.assertEqual(report['acceptance_criteria'], ['保留原业务分类'])
            self.assertEqual(report['terminal_step'], 'prepare_evidence')
            self.assertEqual(report['terminal_reason'], '缺少原始证据')

    def test_finalize_persists_report(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(directory)
            result = run(ctx, FinalizeInput(
                task_id='task', carrier='TEST', biztype='track',
                terminal_status='manual', report_destination=''))
            report = ctx.store.read_artifact('run', 'reports/final.json', 'task')
            self.assertEqual(report['root_cause'], '真实分析结论')
            self.assertEqual(result.delivery_status, 'SKIPPED')

    def test_analysis_delivery_builds_real_report(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(directory)
            ctx.services.delivery.deliver.return_value = {'commit': 'abc', 'local_branch': True}
            data = DeliveryInput(
                task_id='task', carrier='TEST', repair_branch='repair-test',
                ai_workspace_path=str(Path(directory) / 'workspace'),
                analysis_only=True, changed_files=[], report_paths=['gathers/TEST/final.json'])
            result = deliver(ctx, data)
            self.assertEqual(result.commit, 'abc')
            report = ctx.store.read_artifact('run', 'delivery/analysis.json', 'task')
            self.assertEqual(report['root_cause'], '真实分析结论')
            local_report = Path(data.ai_workspace_path) / data.report_paths[0]
            self.assertEqual(json.loads(local_report.read_text(encoding='utf-8')), report)
            self.assertFalse(ctx.services.delivery.deliver.call_args.args[0]['allow_push'])


if __name__ == '__main__':
    unittest.main()
