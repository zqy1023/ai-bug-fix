"""工作区文件过滤与副本写权限。"""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path


class WorkspaceFilterTests(unittest.TestCase):
    """过滤凭据和缓存，同时保留具有相似名称的业务源码。"""

    def test_code_files_no_longer_excluded_by_prefix(self):
        from app.infrastructure.filesystem.workspace import WorkspaceManager

        self.assertFalse(WorkspaceManager.is_sensitive("token.py"))
        self.assertFalse(WorkspaceManager.is_sensitive("auth.py"))
        self.assertFalse(WorkspaceManager.is_sensitive("password.txt"))

    def test_credential_names_still_excluded(self):
        from app.infrastructure.filesystem.workspace import WorkspaceManager

        self.assertTrue(WorkspaceManager.is_sensitive(".env"))
        self.assertTrue(WorkspaceManager.is_sensitive(".env.local"))
        self.assertTrue(WorkspaceManager.is_sensitive(".env.production"))
        self.assertTrue(WorkspaceManager.is_sensitive("credentials"))
        self.assertTrue(WorkspaceManager.is_sensitive("credentials.json"))
        self.assertTrue(WorkspaceManager.is_sensitive("id_rsa"))
        self.assertTrue(WorkspaceManager.is_sensitive("id_ed25519"))
        self.assertTrue(WorkspaceManager.is_sensitive("server.pem"))
        self.assertTrue(WorkspaceManager.is_sensitive("host.key"))
        self.assertTrue(WorkspaceManager.is_sensitive("model.pyc"))

    def test_extensionless_prefix_rules_kept(self):
        from app.infrastructure.filesystem.workspace import WorkspaceManager

        self.assertTrue(WorkspaceManager.is_sensitive("token"))
        self.assertTrue(WorkspaceManager.is_sensitive("token_backup"))
        self.assertTrue(WorkspaceManager.is_sensitive("secrets-dir"))
        self.assertTrue(WorkspaceManager.is_sensitive("auth_provider.csv"))
        self.assertFalse(WorkspaceManager.is_sensitive("main.py"))
        self.assertFalse(WorkspaceManager.is_sensitive("tokenizer.py"))


class RestoreWritePermissionTests(unittest.TestCase):
    """共享树副本恢复写权限，允许业务进程写入缓存与日志。"""

    def test_restore_write_permissions(self):
        from app.workflow.validation_executor import _restore_write_permissions

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "gathers" / "Test"
            nested.mkdir(parents=True)
            target = nested / "main.py"
            target.write_text("x = 1\n", encoding="utf-8")
            os.chmod(target, stat.S_IREAD)
            os.chmod(nested, stat.S_IREAD)
            _restore_write_permissions(root)
            # Windows 上清除只读位后应可直接追加写；目录应可创建新文件。
            with target.open("a", encoding="utf-8"):
                pass
            (nested / "cache.tmp").write_text("ok", encoding="utf-8")
