import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from git import Repo
from workflow.stages.claim_slot.node import _claim_slot, release_claim, _pending_branch

class ClaimClosureTests(unittest.TestCase):
    def test_execution_release_preserves_unmerged_branch_barrier(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with Repo.init(root / 'repo') as repo:
                with repo.config_writer() as config:
                    config.set_value('user', 'name', 'test')
                    config.set_value('user', 'email', 'test@example.invalid')
                file = Path(repo.working_tree_dir) / 'sample.txt'
                file.write_text('baseline', encoding='utf-8')
                repo.index.add(['sample.txt'])
                repo.index.commit('baseline')
                base = repo.active_branch.name
                repo.git.checkout('-b', 'repair-test')
                file.write_text('fixed', encoding='utf-8')
                repo.index.add(['sample.txt'])
                repo.index.commit('repair')
                repo.git.checkout(base)
                def context(run):
                    return SimpleNamespace(store=SimpleNamespace(root=root), run=SimpleNamespace(run_id=run))
                self.assertTrue(_claim_slot(context('first'), str(root / 'repo'), 'repair-test', 'fingerprint', 'task'))
                self.assertFalse(_claim_slot(context('second'), str(root / 'repo'), 'repair-test', 'fingerprint', 'task'))
                release_claim(root, run_id='first', task_id='task')
                self.assertEqual(_pending_branch(repo, 'repair-test', 'origin'), 'repair-test')
                repo.git.merge('--ff-only', 'repair-test')
                self.assertEqual(_pending_branch(repo, 'repair-test', 'origin'), '')
                self.assertTrue(_claim_slot(context('second'), str(root / 'repo'), 'repair-test', 'fingerprint', 'task'))
