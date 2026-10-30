import tempfile
import unittest
from pathlib import Path
from unittest import mock

from git import Repo

from config.settings import settings
from core.errors import StepFailed
from core.keys import repair_branch
from workflow.stages.claim_slot.node import pending_repair_branch

BIZTYPE = "framework.track_server"
CARRIER = "TEST"
PROBLEM_KEY = "abcdef123456"


def branch_name() -> str:
    return repair_branch(BIZTYPE, CARRIER, settings.repair_branch_prefix, PROBLEM_KEY)


class RemoteBranchClaimTests(unittest.TestCase):
    """重复检查只认远端分支；本地分支不参与判定。"""

    def _prepare(self, root: Path) -> Repo:
        repo = Repo.init(root / "repo")
        with repo.config_writer() as config:
            config.set_value("user", "name", "test")
            config.set_value("user", "email", "test@example.invalid")
        (root / "repo" / "sample.txt").write_text("baseline", encoding="utf-8")
        repo.index.add(["sample.txt"])
        repo.index.commit("baseline")
        repo.create_remote("origin", "https://example.invalid/repo.git")
        return repo

    def _commit_branch(self, repo: Repo, branch: str) -> None:
        base = repo.active_branch.name
        repo.git.checkout("-b", branch)
        (Path(repo.working_tree_dir) / "sample.txt").write_text("fixed", encoding="utf-8")
        repo.index.add(["sample.txt"])
        repo.index.commit("repair")
        repo.git.checkout(base)

    def _pending(self, repo: Repo, exists: bool) -> str:
        with mock.patch(
            "workflow.stages.claim_slot.node.remote_branch_exists",
            return_value=exists,
        ):
            return pending_repair_branch(
                str(repo.working_tree_dir),
                biztype=BIZTYPE,
                carrier=CARRIER,
                problem_key=PROBLEM_KEY,
                remote_name="origin",
            )

    def test_remote_branch_blocks_even_without_local_ref(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            repo = self._prepare(Path(directory))
            try:
                self.assertNotIn(branch_name(), {head.name for head in repo.heads})
                self.assertEqual(self._pending(repo, exists=True), branch_name())
            finally:
                repo.close()

    def test_local_leftover_without_remote_branch_does_not_block(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            repo = self._prepare(Path(directory))
            try:
                self._commit_branch(repo, branch_name())
                self.assertIn(branch_name(), {head.name for head in repo.heads})
                self.assertEqual(self._pending(repo, exists=False), "")
            finally:
                repo.close()

    def test_remote_query_failure_is_retryable_not_clear(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            repo = self._prepare(Path(directory))
            try:
                with mock.patch(
                    "workflow.stages.claim_slot.node.remote_branch_exists",
                    side_effect=RuntimeError("network down"),
                ):
                    with self.assertRaises(StepFailed) as raised:
                        pending_repair_branch(
                            str(repo.working_tree_dir),
                            biztype=BIZTYPE,
                            carrier=CARRIER,
                            problem_key=PROBLEM_KEY,
                            remote_name="origin",
                        )
                self.assertEqual(raised.exception.code, "GIT_REMOTE_QUERY_FAILED")
                self.assertEqual(raised.exception.result, "failed")
            finally:
                repo.close()

    def test_repo_without_remote_falls_back_to_local_branch(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            root = Path(directory)
            repo = Repo.init(root / "repo")
            try:
                with repo.config_writer() as config:
                    config.set_value("user", "name", "test")
                    config.set_value("user", "email", "test@example.invalid")
                (root / "repo" / "sample.txt").write_text("baseline", encoding="utf-8")
                repo.index.add(["sample.txt"])
                repo.index.commit("baseline")
                self._commit_branch(repo, branch_name())

                self.assertEqual(
                    pending_repair_branch(
                        str(root / "repo"),
                        biztype=BIZTYPE,
                        carrier=CARRIER,
                        problem_key=PROBLEM_KEY,
                        remote_name="origin",
                    ),
                    branch_name(),
                )
            finally:
                repo.close()


if __name__ == "__main__":
    unittest.main()
