import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.infrastructure.git import source


class SourceCloneProgressTests(unittest.TestCase):
    def test_clone_uses_independent_timeout_and_reports_downloaded_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            mirror = Path(directory) / "mirror"
            config = SimpleNamespace(project_subdir=None, source_url="https://example.invalid/repo.git",
                                     git_clone_timeout_seconds=1800)
            observed = []

            def execute(args, cwd, env, timeout, **kwargs):
                self.assertEqual(timeout, 1800)
                pack = Path(args[-1]) / ".git/objects/pack/partial.pack"
                pack.parent.mkdir(parents=True)
                pack.write_bytes(b"downloaded")
                kwargs["on_progress"](12)
                return ""

            with patch.object(source, "_run_git", side_effect=execute), \
                    patch.object(source, "_apply_sparse_checkout"):
                source._clone(config, mirror, {}, "origin", 300, on_progress=observed.append)
            self.assertTrue(mirror.is_dir())
            self.assertEqual(observed, [{"downloaded_bytes": 10, "elapsed_seconds": 12, "timeout_seconds": 1800}])
