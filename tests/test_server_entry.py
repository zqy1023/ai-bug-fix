import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('workflow_launcher', Path(__file__).resolve().parents[1] / 'server.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)

class ServerEntryTests(unittest.TestCase):
    def test_default_is_one_flow_without_web(self):
        args = launcher._arguments([])
        self.assertFalse(args.web or args.frontend or args.serve_only)

    def test_services_can_start_without_new_run(self):
        args = launcher._arguments(['--serve-only', '--frontend'])
        self.assertTrue(args.serve_only and args.frontend)

    def test_once_prepares_then_uses_api_lifecycle(self):
        with patch('workflow.engine.graph.prepare_run', return_value=('test-run', None, None)) as prepare, patch('server.lifecycle._run_workflow_background_v1') as execute:
            self.assertEqual(launcher._start_once('test-run'), 'test-run')
            prepare.assert_called_once_with('test-run', overrides={}, queued=True)
            execute.assert_called_once_with('test-run', {})
