import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agents.unified_fixer import SPEC
from infrastructure.storage.store import RecordStore
from workflow.engine.graph import _agent_node


class AgentRecordResultTests(unittest.TestCase):
    def test_agent_results_persist_and_handoff_with_current_contract(self):
        cases = [
            ('FIXED', True, 'passed', 'PASS'),
            ('FIXED', False, 'failed', 'FAIL'),
            ('ANALYSIS_ONLY', False, 'passed', 'MANUAL'),
            ('MANUAL', False, 'failed', 'MANUAL'),
            ('INSUFFICIENT_EVIDENCE', False, 'failed', 'INSUFFICIENT_EVIDENCE'),
        ]
        for status, passed, result, expected in cases:
            with self.subTest(status=status, passed=passed), tempfile.TemporaryDirectory() as directory:
                store = RecordStore(directory)
                output = {'status': status, 'passed': passed, 'result': result, 'summary': 'test result'}
                graph = SimpleNamespace(invoke=lambda state: output)
                state = {'run_id': 'test-run', 'task_id': 'test-task', 'attempt': 1,
                         'ai_workspace_path': directory, 'evidence': {},
                         'task': {'carrier': 'TEST', 'biztype': 'track', 'service_name': 'test-service'},
                         'problem': {'carrier': 'TEST', 'biztype': 'track', 'service_name': 'test-service'}}
                patch = _agent_node(SPEC, graph, store)(state)
                record = store.load('test-run', SPEC.name, 'test-task')
                self.assertEqual(record.status, 'succeeded')
                self.assertEqual(record.result, expected)
                self.assertIsNone(record.error)
                self.assertEqual(record.output['status'], status)
                handoff = store.read_artifact('test-run', f'workflow/outputs/{SPEC.name}.json', 'test-task')
                self.assertEqual(handoff['outcome']['assessment']['code'], expected)
                self.assertEqual(patch['unified_fixer']['status'], status)


if __name__ == '__main__':
    unittest.main()
