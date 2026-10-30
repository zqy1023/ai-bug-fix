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
            self.assertEqual(calls[0].budget_tier, 'analysis')
            self.assertEqual(calls[0].budget['max_turns'], 12)
            self.assertEqual(calls[0].budget['max_tool_calls'], 16)

    def test_legacy_entry_budget_and_failed_call(self):
        for framework, dependency in [('spiders', False), ('spiders', True)]:
            with self.subTest(framework=framework), tempfile.TemporaryDirectory() as directory:
                (Path(directory) / framework / 'TEST').mkdir(parents=True)
                calls = []

                def runner(**kwargs):
                    calls.append(kwargs)
                    raise ConnectionError('connection reset')

                state = {'run_id': 'r', 'task_id': 't', 'carrier': 'TEST', 'attempt': 1,
                         'workspace_path': directory,
                         'problem': {'service_name': 'test', 'biztype': 'test', 'carrier': 'TEST'},
                         'evidence': {'framework': framework, 'legacy_dependency': dependency}}
                runtime = SimpleNamespace(config=UnifiedFixerConfig(), agent_runner=runner)
                with patch('agents.unified_fixer.provider.invoke_with_audit', side_effect=lambda req, invoke: invoke()):
                    result = run_unified_fixer(state, runtime)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]['timeout_seconds'], 300)
                self.assertEqual(calls[0]['max_turns'], 12)
                self.assertEqual(calls[0]['max_tool_calls'], 16)
                self.assertFalse(calls[0]['writable'])
                self.assertEqual(result['status'], 'MANUAL')
                self.assertEqual(result['result'], 'failed')

    def test_analysis_budget_respects_tighter_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'spiders/TEST').mkdir(parents=True)
            captured = []

            def factory(config, **kwargs):
                captured.append(config)
                return lambda request: {'result': json.dumps({'status': 'ANALYSIS_ONLY'})}

            state = {'run_id': 'r', 'task_id': 't', 'carrier': 'TEST', 'attempt': 1,
                     'workspace_path': directory,
                     'problem': {'service_name': 'test', 'biztype': 'test', 'carrier': 'TEST'},
                     'evidence': {'framework': 'spiders'}}
            config = UnifiedFixerConfig(timeout_seconds=30, max_turns=1,
                                       max_tool_calls=2, max_context_chars=4000)
            with patch('agents.unified_fixer.graph.create_provider', side_effect=factory):
                run_unified_fixer(state, config)
            self.assertEqual(captured[0].timeout_seconds, 30)
            self.assertEqual(captured[0].max_turns, 1)
            self.assertEqual(captured[0].max_tool_calls, 2)
            self.assertEqual(captured[0].max_context_chars, 4000)
            self.assertEqual(captured[0].max_self_healing_turns, 0)
            self.assertEqual(captured[0].max_provider_attempts, 1)


if __name__ == '__main__':
    unittest.main()
