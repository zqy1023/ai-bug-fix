"""源码同步进度必须经过真实 Git 回调落盘，不能只验证前端模拟数据。"""

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from infrastructure.storage.lifecycle import LifecycleStore
from infrastructure.storage.store import RecordStore
from workflow.engine.graph import _source_sync_node


class SourceProgressTests(unittest.TestCase):
    def test_real_clone_persists_progress_from_git_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            origin = root / 'origin'
            origin.mkdir()
            for args in (
                ['init'],
                ['-c', 'user.name=Progress Test', '-c', 'user.email=progress@example.test',
                 'commit', '--allow-empty', '-m', 'initial'],
            ):
                subprocess.run(['git', '-C', str(origin), *args], check=True, capture_output=True)
            config = SimpleNamespace(
                source_repo=root / 'mirror', source_url=str(origin), project_subdir=None,
                git_remote='origin', git_username='', git_password='',
                git_fetch_timeout_seconds=30, git_clone_timeout_seconds=30,
            )
            store = RecordStore(root / 'runtime')
            result = _source_sync_node(config, store)({'run_id': 'clone-progress'})
            self.assertEqual(result['last_result'], 'passed')
            lifecycle = LifecycleStore(store.root).read_run('clone-progress')
            # _run_git 会捕获上报异常，缺少导入时克隆也会成功，必须检查落盘数据。
            self.assertIn('progress', lifecycle)
            progress = lifecycle['progress']
            self.assertEqual(progress['step'], 'sync_source_repo')
            self.assertIn('已下载', progress['message'])
            self.assertGreaterEqual(progress['counts']['downloaded_bytes'], 0)
            self.assertEqual(progress['counts']['timeout_seconds'], 30)
            self.assertEqual(progress['counts']['automatic_attempt'], 1)
            self.assertTrue(lifecycle['progress_at'])
            # 第二轮复用已有镜像走 fetch，同样必须刷新进度，不能只覆盖首次克隆。
            _source_sync_node(config, store)({'run_id': 'fetch-progress'})
            fetch = LifecycleStore(store.root).read_run('fetch-progress')['progress']
            self.assertEqual(fetch['counts']['fetching'], 1)
            self.assertIn('拉取源码更新', fetch['message'])

    def test_progress_updates_are_readable_while_step_is_running(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RecordStore(directory)
            config = SimpleNamespace(source_repo=Path(directory) / 'mirror', source_url='local-fixture')

            def sync(_config, *, cancel_requested, on_progress):
                self.assertFalse(cancel_requested())
                for downloaded in (0, 15 * 1024 * 1024):
                    on_progress({'downloaded_bytes': downloaded, 'elapsed_seconds': 12, 'timeout_seconds': 600})
                    self.assertEqual(store.load('run', 'sync_source_repo').status, 'running')
                    live = LifecycleStore(store.root).read_run('run')
                    self.assertEqual(live['progress']['counts']['downloaded_bytes'], downloaded)
                    self.assertEqual(live['current_step'], 'sync_source_repo')
                return config.source_repo

            with patch('workflow.engine.graph.sync_source_repo', side_effect=sync):
                _source_sync_node(config, store)({'run_id': 'run'})


if __name__ == '__main__':
    unittest.main()
