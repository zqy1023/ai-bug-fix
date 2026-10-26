"""全流程只保留当前入口，旧路由、旧记录及旧验证插件不可再次启用。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domain.approval import APPROVAL_RETRY_TARGETS
from app.infrastructure.records.database import model_calls
from app.infrastructure.records.lifecycle import LifecycleStore
from app.infrastructure.records.store import RecordStore
from app.workflow.definitions import REVIEW_EVIDENCE_STEPS, TASK_NODE_NAMES
from app.workflow.plugin_loader import load_all, load_step
from app.workflow.registry import PluginError
from app.workflow.validation import assert_workflow_version
from workbench.api import router


class CurrentProtocolTests(unittest.TestCase):
    def test_unversioned_http_routes_are_absent(self):
        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/health").status_code, 404)
            self.assertEqual(client.get("/api/runs").status_code, 404)
            self.assertEqual(client.post("/api/runs", json={}).status_code, 404)
        self.assertTrue(all(not route.path.startswith("/api/") or route.path.startswith("/api/v1/") for route in app.routes))

    def test_internal_validators_are_not_public_workflow_plugins(self):
        load_all()
        for name in REVIEW_EVIDENCE_STEPS:
            with self.subTest(name=name), self.assertRaises(PluginError):
                load_step(name)
        self.assertFalse(set(TASK_NODE_NAMES) & set(REVIEW_EVIDENCE_STEPS))

    def test_approval_cannot_retry_removed_nodes(self):
        self.assertEqual(APPROVAL_RETRY_TARGETS, {"locate_agent", "repair_agent", "review_agent", "prepare_delivery"})

    def test_previous_workflow_generation_is_rejected(self):
        with self.assertRaises(ValueError):
            assert_workflow_version({"workflow_version": 6})
        assert_workflow_version({"workflow_version": 7})

    def test_step_record_is_not_upgraded_from_v0(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RecordStore(directory)
            store.run_dir("run").mkdir(parents=True)
            store.steps_path("run").write_text(json.dumps({"locate_agent": {"name": "locate_agent", "schema_version": 0, "status": "passed"}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "协议版本不受支持"):
                store.load("run", "locate_agent")

    def test_model_calls_do_not_read_old_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "model_calls.jsonl").write_text('{"task_id":"task","model":"obsolete"}\n', encoding="utf-8")
            self.assertEqual(model_calls(root), [])

    def test_lifecycle_does_not_read_old_json_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "runs" / "run"
            root.mkdir(parents=True)
            (root / "lifecycle.json").write_text('{"status":"running"}', encoding="utf-8")
            self.assertEqual(LifecycleStore(directory).read_run("run"), {})
