"""步骤输入校验与畸形任务记录处理。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.domain.errors import StepFailed


class InputBuilderDefenseTests(unittest.TestCase):
    """步骤缺少上下文时返回结构化错误，完整输入正常构造。"""

    def _approval_module(self):
        import app.workflow.steps.request_approval as module

        return module

    def test_request_approval_missing_candidate(self):
        module = self._approval_module()
        with self.assertRaises(StepFailed) as caught:
            module._input({"task": {"carrier": "Test"}})
        self.assertEqual(caught.exception.code, "APPROVAL_CONTEXT_MISSING")
        self.assertIn("delivery_candidate", str(caught.exception))

    def test_request_approval_missing_workspace(self):
        module = self._approval_module()
        with self.assertRaises(StepFailed) as caught:
            module._input({"delivery_candidate": {"candidate_id": "c-1"}})
        self.assertEqual(caught.exception.code, "APPROVAL_CONTEXT_MISSING")
        self.assertIn("ai_workspace_path", str(caught.exception))

    def test_request_approval_success_path(self):
        module = self._approval_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            baseline = root / "baseline"
            for base in (workspace, baseline):
                path = base / "gathers" / "Test" / "main.py"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("print('ok')\n", encoding="utf-8")
            state = {
                "task": {"carrier": "Test"},
                "task_id": "test__gather__abc",
                "delivery_candidate": {"candidate_id": "c-1"},
                "ai_workspace_path": str(workspace),
                "baseline_dir": str(baseline),
                "changed_files": ["gathers/Test/main.py"],
                "baseline_manifest": {"gathers/Test/main.py": "x"},
            }
            payload = module._input(state)
        self.assertEqual(payload["candidate_id"], "c-1")
        self.assertEqual(payload["carrier"], "Test")
        self.assertEqual(payload["decision"], "PENDING")
        self.assertRegex(payload["approval_fingerprint"], r"^[0-9a-f]{64}$")

    def test_define_acceptance_missing_key(self):
        from app.workflow.steps.define_acceptance import SPEC

        with self.assertRaises(StepFailed) as caught:
            SPEC.input_builder({"problem": {"error": "x"}})
        self.assertEqual(caught.exception.code, "ACCEPTANCE_CONTEXT_MISSING")
        self.assertIn("baseline_dir", str(caught.exception))

    def test_define_acceptance_success_path(self):
        from app.workflow.steps.define_acceptance import SPEC

        locate = {"candidate_files": ["gathers/Test/main.py"]}
        payload = SPEC.input_builder(
            {
                "problem": {"error": "boom"},
                "baseline_dir": "b",
                "baseline_manifest": {},
                "locate": locate,
            }
        )
        self.assertEqual(payload["problem"], {"error": "boom"})
        self.assertEqual(payload["diagnosis"], locate)

    def test_reproduce_baseline_missing_key(self):
        from app.workflow.steps.reproduce_baseline import input_of

        state = {
            "task_id": "t",
            "problem": {},
            "baseline_dir": "b",
            "baseline_manifest": {},
            "source_project_path": "s",
            "ai_workspace_path": "w",
            "task": {"carrier": "Test"},
            "locate": {},
        }
        with self.assertRaises(StepFailed) as caught:
            input_of(state)
        self.assertEqual(caught.exception.code, "REPRODUCE_CONTEXT_MISSING")
        self.assertIn("acceptance_contract", str(caught.exception))

    def test_reproduce_baseline_missing_task(self):
        from app.workflow.steps.reproduce_baseline import input_of

        state = {
            key: "x"
            for key in (
                "task_id",
                "problem",
                "baseline_dir",
                "baseline_manifest",
                "source_project_path",
                "ai_workspace_path",
                "acceptance_contract",
            )
        }
        state["locate"] = {}
        with self.assertRaises(StepFailed) as caught:
            input_of(state)
        self.assertEqual(caught.exception.code, "REPRODUCE_CONTEXT_MISSING")

    def test_reproduce_baseline_success_path(self):
        from app.workflow.steps.reproduce_baseline import input_of

        state = {
            "task_id": "t",
            "problem": {"error": "boom"},
            "baseline_dir": "b",
            "baseline_manifest": {"a.py": "h"},
            "source_project_path": "s",
            "ai_workspace_path": "w",
            "acceptance_contract": {"kind": "baseline"},
            "task": {"carrier": "Test"},
            "locate": {"candidate_files": ["a.py"]},
        }
        payload = input_of(state)
        self.assertEqual(payload["carrier"], "Test")
        self.assertEqual(payload["candidates"], ["a.py"])
        self.assertEqual(payload["acceptance_contract"], {"kind": "baseline"})


class IdentifyCarrierDefenseTests(unittest.TestCase):
    """畸形任务记录计入人工处理，避免中断任务识别。"""

    def _record(self):
        return {
            "service_name": "gather.py.track",
            "biztype": "gather",
            "carrier": "Test",
            "dedup_key": "k1",
            "representative_index": 0,
            "problem": {
                "service_name": "gather.py.track",
                "biztype": "gather",
                "carrier": "Test",
                "req": "{}",
                "error": "boom",
                "raw_error": "boom",
            },
        }

    def test_ok_accepts_wellformed_record(self):
        from app.workflow.steps.identify_carrier.node import _ok

        self.assertTrue(_ok(self._record()))

    def test_ok_rejects_malformed_records(self):
        from app.workflow.steps.identify_carrier.node import _ok

        base = self._record()
        self.assertFalse(_ok({**base, "problem": None}))
        self.assertFalse(_ok({**base, "problem": {"service_name": "x"}}))
        broken = dict(base)
        broken.pop("representative_index")
        self.assertFalse(_ok(broken))
        self.assertFalse(_ok({**base, "service_name": ""}))

    def test_run_counts_malformed_records_as_manual(self):
        import app.workflow.steps.identify_carrier as module

        good = self._record()
        bad = {"service_name": "gather.py.track", "biztype": "gather", "carrier": "Test"}
        normalize_record = SimpleNamespace(
            status="succeeded",
            output={"record_count": 2, "ignored_count": 0, "duplicate_count": 0, "unique_count": 2},
            artifacts=[SimpleNamespace(name="normalized_records")],
        )
        ctx = SimpleNamespace(
            store=SimpleNamespace(load=lambda *args: normalize_record),
            load_artifact=lambda ref: {"records": [good, bad], **normalize_record.output},
            run=SimpleNamespace(run_id="run-1"),
        )
        output = module.run(ctx, module.IdentifyCarrierInput(run_id="run-1"))
        self.assertEqual(output.manual, 1)
        self.assertEqual(output.created, 1)
        self.assertEqual(output.tasks[0].carrier, "Test")
