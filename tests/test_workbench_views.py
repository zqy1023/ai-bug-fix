"""操作门禁和正式交付投影的 API 回归；所有记录均在临时目录。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.infrastructure.records.store import RecordStore
from app.application.audit import StepRecord
from app.workflow.contracts import Assessment, ExecutionResult, Handoff, StepOutcome, TaskSnapshot, WORKFLOW_VERSION
from workbench import api
from workbench.view_models import delivery_view, run_actions, task_retry_action


class WorkbenchViewTests(unittest.TestCase):
    def test_candidate_is_not_published(self):
        candidate = {"name": "prepare_delivery", "status": "succeeded", "output": {
            "candidate_id": "candidate", "commit": "abc", "repair_branch": "fix", "local_branch": True}}
        self.assertEqual(delivery_view([candidate]).phase, "prepared")
        published = {**candidate, "name": "publish_delivery"}
        self.assertEqual(delivery_view([candidate, published]).phase, "published")
        self.assertEqual(delivery_view([candidate, {**published, "status": "failed"}]).phase, "failed")

    def test_retry_boundaries(self):
        base = dict(current=True, worker_active=False, run_failed=False, run_status="failed",
                    task={"result": "FAILED", "steps": [{"name": "finalize_execution"}]})
        self.assertTrue(task_retry_action(**base).enabled)
        for change in ({"current": False}, {"worker_active": True}, {"run_failed": True},
                       {"task": {"result": "FAILED", "approval_pending": True}},
                       {"task": {"result": "FAILED", "steps": []}},
                       {"task": {"result": "FAILED", "steps": [{"name": "finalize_execution"}, {"name": "prepare_delivery"}]}}):
            with self.subTest(change=change):
                action = task_retry_action(**{**base, **change})
                self.assertFalse(action.enabled)
                self.assertTrue(action.reason)

    def test_run_retry_requires_resume_or_safe_startup(self):
        base = dict(current=True, status="failed", worker_active=False, resumable=False, safe_startup=False)
        self.assertFalse(run_actions(**base)["retry"].enabled)
        self.assertTrue(run_actions(**{**base, "safe_startup": True})["retry"].enabled)
        self.assertFalse(run_actions(**{**base, "safe_startup": True, "current": False})["retry"].enabled)

    def test_task_http_uses_current_formal_output(self):
        with TemporaryDirectory() as directory:
            store = RecordStore(Path(directory))
            root = store.run_dir("run")
            root.mkdir(parents=True, exist_ok=True)
            metadata = root / "run.json"
            metadata.write_text(json.dumps({"workflow_version": WORKFLOW_VERSION}), encoding="utf-8")
            output = {"candidate_id": "complete-candidate", "commit": "complete-commit", "repair_branch": "fix"}
            handoff = Handoff(step="prepare_delivery", state=TaskSnapshot.from_graph({"run_id": "run", "task_id": "task"}),
                              outcome=StepOutcome(execution=ExecutionResult(status="succeeded"),
                                                  assessment=Assessment(outcome="passed", code="PREPARED", summary="本地候选"),
                                                  output=output))
            store.write_artifact("run", "workflow/outputs/prepare_delivery.json", handoff.model_dump(mode="json"), "task")
            steps = [{"name": "prepare_delivery", "status": "succeeded", "output": {"commit": "truncated"}}]
            payload = {"tasks": [{"task_id": "task", "status": "FAILED", "steps": steps}]}
            app = FastAPI()
            app.include_router(api.router)
            app.dependency_overrides[api._authorize] = lambda: None
            config = SimpleNamespace(run_parameters=lambda _: {})
            with patch.object(api, "_store", return_value=store), patch.object(api, "_config", return_value=config), \
                    patch.object(api, "_run_payload", return_value=payload), \
                    patch.object(api, "_v1_run_summary", return_value={"status": "failed"}), TestClient(app) as client:
                response = client.get("/api/v1/runs/run/tasks/task")
                self.assertEqual(response.status_code, 200, response.text)
                detail = response.json()
                self.assertEqual(detail["delivery"]["phase"], "prepared")
                self.assertEqual(detail["delivery"]["commit"], "complete-commit")
                self.assertEqual(detail["task"]["steps"][0]["assessment"]["outcome"], "passed")
                self.assertFalse(detail["actions"]["retry"]["enabled"])
                pending = {"items": [{"run_id": "run", "task_id": "task", "approval_step": {}}]}
                with patch.object(api, "_pending_approvals", return_value=pending):
                    response = client.get("/api/v1/approvals?status=pending")
                self.assertEqual(response.status_code, 200, response.text)
                approval = response.json()["items"][0]
                self.assertEqual(approval["delivery"]["candidate_id"], "complete-candidate")
                self.assertTrue(approval["actions"]["approve"]["enabled"])

    def test_step_http_does_not_offer_rerun_for_approval(self):
        with TemporaryDirectory() as directory:
            store = RecordStore(Path(directory))
            root = store.run_dir("run")
            root.mkdir(parents=True, exist_ok=True)
            (root / "run.json").write_text(json.dumps({"workflow_version": WORKFLOW_VERSION}), encoding="utf-8")
            store.save("run", StepRecord(name="request_approval", title="人工审批", status="waiting"), "task")
            app = FastAPI()
            app.include_router(api.router)
            app.dependency_overrides[api._authorize] = lambda: None
            with patch.object(api, "_store", return_value=store), patch.object(api, "_run_payload", return_value={}), \
                    patch.object(api, "_v1_detail", return_value={"status": "failed", "worker_active": False}), \
                    patch.object(api, "_load_model_calls", return_value=[]), TestClient(app) as client:
                response = client.get("/api/v1/runs/run/tasks/task/steps/request_approval")
                self.assertEqual(response.status_code, 200, response.text)
                action = response.json()["actions"]["rerun"]
                self.assertFalse(action["enabled"])
                self.assertIn("审批", action["reason"])
