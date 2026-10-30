import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from server.routes import runs


class SerialStartTests(unittest.TestCase):
    def test_active_run_blocks_new_start(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SimpleNamespace(root=Path(directory))
            with patch.object(runs, '_all_run_ids', return_value=['run-1']), patch(
                'infrastructure.storage.lifecycle.LifecycleStore.read_run',
                return_value={'status': 'running'},
            ):
                self.assertEqual(runs._busy_run(store), ('run-1', 'running'))

    def test_terminal_run_does_not_block_new_start(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SimpleNamespace(root=Path(directory))
            with patch.object(runs, '_all_run_ids', return_value=['run-1']), patch(
                'infrastructure.storage.lifecycle.LifecycleStore.read_run',
                return_value={'status': 'failed'},
            ):
                self.assertIsNone(runs._busy_run(store))

    def test_waiting_approval_does_not_block_after_approval_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SimpleNamespace(root=Path(directory))
            with patch.object(runs, '_all_run_ids', return_value=['run-1']), patch(
                'infrastructure.storage.lifecycle.LifecycleStore.read_run',
                return_value={'status': 'waiting_approval'},
            ):
                self.assertIsNone(runs._busy_run(store))


if __name__ == '__main__':
    unittest.main()
