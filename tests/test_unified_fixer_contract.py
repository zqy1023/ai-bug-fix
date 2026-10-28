import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents.unified_fixer.graph import run_unified_fixer, build_graph
from agents.unified_fixer.models import UnifiedFixerConfig


class UnifiedFixerContractTests(unittest.TestCase):
    def test_real_contract_reaches_runner_and_self_heals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            carrier = root / 'gathers' / 'TEST'
            carrier.mkdir(parents=True)
            target = carrier / 'main.py'
            calls = []

            def runner(**kwargs):
                calls.append(kwargs)
                target.write_text('def broken(:' if len(calls) == 1 else 'value = 1\n')
                return {'result': json.dumps({'status': 'FIXED', 'changed_files': ['gathers/TEST/main.py']})}

            runtime = SimpleNamespace(config=UnifiedFixerConfig(max_turns=4), agent_runner=runner)
            state = {'run_id': 'test-run', 'task_id': 'test-task', 'attempt': 1,
                     'carrier': 'TEST', 'workspace_path': directory,
                     'problem': {'service_name': 'test', 'biztype': 'test', 'carrier': 'TEST'},
                     'evidence': {'framework': 'gathers'}, 'agent_policy': {'unified_model': 'test-model'}}
            with patch('agents.unified_fixer.provider.invoke_with_audit', side_effect=lambda req, invoke: invoke()):
                result = run_unified_fixer(state, runtime)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0]['env']['ANTHROPIC_MODEL'], 'test-model')
            self.assertEqual(calls[0]['max_turns'], 4)
            self.assertIn('系统自检反馈', calls[1]['prompt'])
            self.assertTrue(result['passed'])
            self.assertTrue((root / 'artifacts/analysis.json').is_file())

    def test_compiled_graph_uses_current_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'spiders/TEST').mkdir(parents=True)
            state = {'run_id': 'test-run', 'task_id': 'test-task', 'attempt': 1,
                     'carrier': 'TEST', 'workspace_path': directory,
                     'problem': {'service_name': 'test', 'biztype': 'test', 'carrier': 'TEST'},
                     'evidence': {'framework': 'spiders'}, 'agent_policy': {'unified_model': 'test-model'}}
            payload = {'status': 'FIXED', 'changed_files': ['spiders/TEST/main.py'],
                       'root_cause': '从源码确认的真实原因', 'source_evidence': [{'path': 'spiders/TEST/main.py', 'note': '证据'}]}
            calls = []
            def provider(request):
                calls.append(request)
                return {'result': json.dumps(payload)}
            with patch('agents.unified_fixer.graph.create_provider', return_value=provider):
                result = build_graph(UnifiedFixerConfig()).invoke(state)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].allowed_write_roots, ())
            self.assertIsNone(calls[0].write_root)
            self.assertTrue(any('spiders' in root for root in calls[0].allowed_read_roots))
            self.assertEqual(result['changed_files'], [])
            self.assertEqual(result['root_cause'], payload['root_cause'])
            self.assertEqual(result['source_evidence'], payload['source_evidence'])
            self.assertEqual(result['status'], 'ANALYSIS_ONLY')


if __name__ == '__main__':
    unittest.main()
