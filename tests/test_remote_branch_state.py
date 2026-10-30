import tempfile
import unittest
from pathlib import Path
from unittest import mock

from git import Repo

from infrastructure.git.branches import (
    delete_local_branch,
    remote_branch_exists,
    remote_branch_head,
)

BRANCH = "ai-bug-tasks/framework.track_server/TEST-abcdef12"


def test_delivery_anchor_keeps_long_task_identity_with_short_filesystem_component(tmp_path):
    from infrastructure.git.delivery import GitBranchDelivery
    task = 'Carrier__gather_providers.legacy_spider_provider.' * 12
    first = GitBranchDelivery._anchor_ref('a' * 32, task, BRANCH, 'candidate')
    second = GitBranchDelivery._anchor_ref('a' * 32, task + 'other', BRANCH, 'candidate')
    assert first != second
    assert max(map(len, first.split('/'))) < 128
    repo = Repo.init(tmp_path)
    (tmp_path / 'baseline.txt').write_text('baseline', encoding='utf-8')
    repo.index.add(['baseline.txt'])
    commit = repo.index.commit('baseline')
    repo.git.update_ref(first, commit.hexsha)
    repo.git.update_ref(second, commit.hexsha)
    assert repo.git.rev_parse(first).strip() == commit.hexsha
    assert repo.git.rev_parse(second).strip() == commit.hexsha


class RemoteBranchQueryTests(unittest.TestCase):
    """远端查询走 ls-remote，不复用本地 origin/<branch> 缓存。"""

    def test_returns_remote_head_and_ignores_local_refs(self):
        repo = mock.MagicMock()
        repo.git.ls_remote.return_value = f"{'a' * 40}\trefs/heads/{BRANCH}\n"

        self.assertEqual(remote_branch_head(repo, "origin", BRANCH), "a" * 40)
        repo.git.ls_remote.assert_called_once_with(
            "--heads", "origin", f"refs/heads/{BRANCH}", kill_after_timeout=60
        )

    def test_missing_branch_is_absence(self):
        repo = mock.MagicMock()
        repo.git.ls_remote.return_value = ""

        self.assertIsNone(remote_branch_head(repo, "origin", BRANCH))
        self.assertFalse(remote_branch_exists(repo, "origin", BRANCH))

    def test_credential_environment_is_injected(self):
        repo = mock.MagicMock()
        repo.git.ls_remote.return_value = f"{'b' * 40}\trefs/heads/{BRANCH}\n"

        self.assertTrue(
            remote_branch_exists(
                repo, "origin", BRANCH, env={"GIT_ASKPASS": "/tmp/askpass"}
            )
        )
        repo.git.custom_environment.assert_called_once_with(GIT_ASKPASS="/tmp/askpass")


class DeleteLocalBranchTests(unittest.TestCase):
    def _prepare(self, root: Path) -> tuple[Repo, str, str]:
        repo = Repo.init(root / "repo")
        with repo.config_writer() as config:
            config.set_value("user", "name", "test")
            config.set_value("user", "email", "test@example.invalid")
        (root / "repo" / "sample.txt").write_text("baseline", encoding="utf-8")
        repo.index.add(["sample.txt"])
        repo.index.commit("baseline")
        base = repo.active_branch.name
        repo.git.checkout("-b", BRANCH)
        (root / "repo" / "sample.txt").write_text("fixed", encoding="utf-8")
        repo.index.add(["sample.txt"])
        repo.index.commit("repair")
        commit = repo.head.commit.hexsha
        repo.git.checkout(base)
        return repo, base, commit

    def test_only_removes_branch_at_expected_commit(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            repo, _base, commit = self._prepare(Path(directory))
            try:
                self.assertFalse(
                    delete_local_branch(repo, BRANCH, expected_commit="0" * 40)
                )
                self.assertIn(BRANCH, {head.name for head in repo.heads})

                self.assertTrue(delete_local_branch(repo, BRANCH, expected_commit=commit))
                self.assertNotIn(BRANCH, {head.name for head in repo.heads})
                self.assertFalse(delete_local_branch(repo, BRANCH, expected_commit=commit))
            finally:
                repo.close()


class WindowsLsRemoteFallbackTests(unittest.TestCase):
    """Windows 上 GitPython 不支持 kill_after_timeout，回退路径必须能解码输出。"""

    def test_fallback_decodes_process_output(self):
        from git.exc import GitCommandError

        from infrastructure.git.branches import _ls_remote

        class Process:
            returncode = 0

            def communicate(self, timeout=None):
                return b"c" * 40 + b"\trefs/heads/x\n", b""

        repo = mock.MagicMock()
        repo.git.ls_remote.side_effect = [
            GitCommandError(
                ["git", "ls-remote"],
                128,
                b"kill_after_timeout feature is not supported on Windows",
            ),
            Process(),
        ]

        self.assertEqual(
            _ls_remote(repo, "origin", "refs/heads/x", 60),
            f"{'c' * 40}\trefs/heads/x\n",
        )


class PublishedStateCleanupTests(unittest.TestCase):
    """推送成功后只保留远端状态：本地候选分支与交付锚点一并清掉。"""

    def test_drop_published_local_state_removes_branch_and_anchor(self):
        from infrastructure.git.delivery import GitBranchDelivery

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
                base = repo.active_branch.name
                repo.git.checkout("-b", BRANCH)
                (root / "repo" / "sample.txt").write_text("fixed", encoding="utf-8")
                repo.index.add(["sample.txt"])
                repo.index.commit("repair")
                commit = repo.head.commit.hexsha
                repo.git.checkout(base)

                delivery = GitBranchDelivery()
                anchor = delivery._anchor_ref("run", "task", BRANCH, "fingerprint")
                repo.git.update_ref(anchor, commit)

                delivery._drop_published_local_state(
                    repo, BRANCH, run_id="run", task_id="task", commit=commit
                )

                self.assertNotIn(BRANCH, {head.name for head in repo.heads})
                self.assertFalse(
                    [ref.path for ref in repo.refs if ref.path.startswith("refs/ai-bug-fixer/")]
                )
            finally:
                repo.close()


class PrepareStageTests(unittest.TestCase):
    """准备阶段只保留锚点；只有正式交付且无法推送时才写本地分支。"""

    def _prepare(self, root: Path) -> tuple[Repo, str]:
        repo = Repo.init(root / "repo")
        with repo.config_writer() as config:
            config.set_value("user", "name", "test")
            config.set_value("user", "email", "test@example.invalid")
        (root / "repo" / "sample.txt").write_text("baseline", encoding="utf-8")
        repo.index.add(["sample.txt"])
        repo.index.commit("baseline")
        base = repo.active_branch.name
        repo.git.checkout("-b", BRANCH)
        (root / "repo" / "sample.txt").write_text("fixed", encoding="utf-8")
        repo.index.add(["sample.txt"])
        repo.index.commit("repair")
        commit = repo.head.commit.hexsha
        repo.git.checkout(base)
        # 准备阶段不建本地分支，测试基线也保持一致。
        repo.git.branch("-D", BRANCH)
        return repo, commit

    def _receipt(self, commit: str) -> dict[str, str]:
        return {
            "repair_branch": BRANCH,
            "commit": commit,
            "fingerprint": "fingerprint",
            "anchor": "refs/ai-bug-fixer/run-task/abcdef0123456789",
        }

    def test_prepare_resume_does_not_create_local_branch(self):
        from infrastructure.git.delivery import GitBranchDelivery

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            repo, commit = self._prepare(Path(directory))
            try:
                receipt = self._receipt(commit)
                repo.git.update_ref(receipt["anchor"], commit)
                result = GitBranchDelivery()._resume_delivery(
                    repo,
                    {"run_id": "run", "task_id": "task"},
                    receipt,
                    materialize_local_branch=False,
                )
                self.assertFalse(result["local_branch"])
                self.assertFalse(result["pushed"])
                self.assertNotIn(BRANCH, {head.name for head in repo.heads})
            finally:
                repo.close()

    def test_prepare_resume_reanchors_when_anchor_missing(self):
        from infrastructure.git.delivery import GitBranchDelivery

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            repo, commit = self._prepare(Path(directory))
            try:
                receipt = self._receipt(commit)
                # 审批打回会删锚点，但提交对象仍在；重试时重新锚定，不该失败。
                result = GitBranchDelivery()._resume_delivery(
                    repo,
                    {"run_id": "run", "task_id": "task"},
                    receipt,
                    materialize_local_branch=False,
                )
                self.assertFalse(result["local_branch"])
                self.assertEqual(str(repo.git.rev_parse(receipt["anchor"])).strip(), commit)
            finally:
                repo.close()

    def test_publish_resume_writes_local_branch_when_push_disabled(self):
        from infrastructure.git.delivery import GitBranchDelivery

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            repo, commit = self._prepare(Path(directory))
            try:
                receipt = self._receipt(commit)
                repo.git.update_ref(receipt["anchor"], commit)
                result = GitBranchDelivery()._resume_delivery(
                    repo,
                    {"run_id": "run", "task_id": "task"},
                    receipt,
                    materialize_local_branch=True,
                )
                self.assertTrue(result["local_branch"])
                self.assertFalse(result["pushed"])
                self.assertEqual(
                    next(head.commit.hexsha for head in repo.heads if head.name == BRANCH),
                    commit,
                )
            finally:
                repo.close()


if __name__ == "__main__":
    unittest.main()
