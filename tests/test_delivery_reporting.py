"""提交者身份、交付终态及旧框架报告路径。"""

from __future__ import annotations

import dataclasses
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class CommitterIdentityTests(unittest.TestCase):
    """提交者身份配置字段与环境注入。"""

    def test_runtime_config_has_committer_and_proxy_fields(self):
        from app.infrastructure.config import RuntimeConfig

        fields = {item.name: item for item in dataclasses.fields(RuntimeConfig)}
        self.assertEqual(fields["git_committer_name"].default, "ai-bug-fixer")
        self.assertEqual(fields["git_committer_email"].default, "ai-bug-fixer@localhost")
        self.assertEqual(fields["proxy_manager_address"].default, "")

    def test_committer_identity_reads_env_with_defaults(self):
        from app.infrastructure.git.delivery import _committer_identity

        clean = {k: v for k, v in os.environ.items()
                 if k not in {"GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"}}
        with patch.dict(os.environ, clean, clear=True):
            identity = _committer_identity()
        self.assertEqual(identity["GIT_AUTHOR_NAME"], "ai-bug-fixer")
        self.assertEqual(identity["GIT_AUTHOR_EMAIL"], "ai-bug-fixer@localhost")
        self.assertEqual(identity["GIT_COMMITTER_NAME"], "ai-bug-fixer")

        with patch.dict(os.environ, clean, clear=True):
            os.environ["GIT_COMMITTER_NAME"] = "team-bot"
            os.environ["GIT_COMMITTER_EMAIL"] = "bot@example.com"
            identity = _committer_identity()
        self.assertEqual(identity["GIT_AUTHOR_NAME"], "team-bot")
        self.assertEqual(identity["GIT_COMMITTER_EMAIL"], "bot@example.com")


def _record(name, status, *, result=None, output=None, record_input=None, attempt=1):
    from app.application.audit import StepRecord

    return StepRecord(
        name=name,
        title=name,
        status=status,
        result=result,
        attempt=attempt,
        started_at=f"2026-01-01T00:0{attempt}:00",
        finished_at=f"2026-01-01T00:0{attempt}:30",
        output=output or {},
        input=record_input or {},
    )


class DeliveryStatusConsistencyTests(unittest.TestCase):
    """交付成功优先于历史失败，终态与原因保持一致。"""

    def _approval_records(self):
        return [
            _record("check_changes", "failed", attempt=1),
            _record("check_changes", "succeeded", attempt=2),
            _record(
                "publish_delivery",
                "succeeded",
                output={"commit": "a" * 40, "pushed": True},
            ),
            _record("request_approval", "succeeded", output={"decision": "APPROVE"}),
        ]

    def test_approval_and_delivery_take_precedence_over_old_failed_records(self):
        from app.workflow.steps.finalize_execution.node import (
            _delivery_status,
            _terminal_details,
        )

        records = self._approval_records()
        result = _delivery_status(records, "")
        self.assertEqual(result, "SUCCESS")
        step, reason, _next = _terminal_details(records, "", result)
        self.assertEqual(step, "request_approval")
        self.assertIn("人工审批", reason)

    def test_pure_failure_unchanged(self):
        from app.workflow.steps.finalize_execution.node import (
            _delivery_status,
            _terminal_details,
        )

        records = [_record("run_regression", "failed", attempt=1)]
        result = _delivery_status(records, "")
        self.assertEqual(result, "FAILED")
        step, reason, _next = _terminal_details(records, "", result)
        self.assertEqual(step, "run_regression")

    def test_cancellation_unchanged(self):
        from app.workflow.steps.finalize_execution.node import (
            _delivery_status,
            _terminal_details,
        )

        records = [_record("check_changes", "failed", attempt=1)]
        self.assertEqual(_delivery_status(records, "CANCELLED"), "CANCELLED")
        step, reason, _next = _terminal_details(records, "CANCELLED", "CANCELLED")
        self.assertEqual(reason, "收到停止请求，任务已在安全节点边界终止。")


class RetrySuccessOverridesHistoricalFailureTests(unittest.TestCase):
    """重试成功后，旧尝试遗留的失败记录不得把终态改写回旧报错。"""

    def _retry_success_records(self):
        return [
            _record("run_regression", "failed", attempt=1),
            _record("locate_agent", "succeeded", attempt=2),
            _record("repair_agent", "succeeded", attempt=2),
            _record(
                "publish_delivery",
                "succeeded",
                attempt=2,
                output={"commit": "a" * 40, "pushed": True},
            ),
        ]

    def test_delivery_status_ignores_failures_predating_successful_publish(self):
        from app.workflow.steps.finalize_execution.node import _delivery_status

        self.assertEqual(_delivery_status(self._retry_success_records(), ""), "SUCCESS")

    def test_terminal_details_short_circuits_success_over_old_failure(self):
        from app.workflow.steps.finalize_execution.node import _terminal_details

        records = self._retry_success_records()
        step, reason, _next = _terminal_details(records, "", "SUCCESS")
        self.assertNotIn("run_regression", step)
        self.assertNotIn("失败", reason)

    def test_failure_after_publish_is_not_treated_as_historical(self):
        from app.workflow.steps.finalize_execution.node import _delivery_status

        records = [
            _record(
                "publish_delivery",
                "succeeded",
                output={"commit": "a" * 40, "pushed": True},
            ),
            _record("run_regression", "failed", attempt=2),
        ]
        self.assertEqual(_delivery_status(records, ""), "FAILED")

    def test_run_error_gated_by_lifecycle_status(self):
        from workbench.projections import run_error

        failure = {"message": "旧报错"}
        for status in ("succeeded", "running", "retrying", "cancelled", "waiting_approval"):
            self.assertEqual(run_error(failure, {"status": status}), {})
        self.assertEqual(run_error(failure, {"status": "failed"})["message"], "旧报错")
        self.assertEqual(
            run_error(failure, {"status": "partial_failed"})["message"], "旧报错"
        )
        self.assertEqual(run_error({}, {"status": "partial_failed"}), {})
        # 旧运行可能没有 lifecycle 记录，失败文件仍要能显示。
        self.assertEqual(run_error(failure, {})["message"], "旧报错")

    def test_task_report_prefers_current_attempt_conclusion(self):
        from app.entrypoint import _ensure_task_final_report

        finalize_output = {
            "delivery_status": "SUCCESS",
            "terminal_reason": "源码修复分支已提交，并通过最终人工审批。",
            "terminal_step": "publish_delivery",
            "next_action": "请在 Git 上检查并手动合并分支；工作流不会自动合并。",
        }
        records = [
            _record("locate_agent", "succeeded", attempt=2),
            _record("repair_agent", "succeeded", attempt=2),
            _record("finalize_execution", "succeeded", attempt=2, output=finalize_output),
        ]
        store = SimpleNamespace(list_steps=lambda run_id, task_id: records)
        report = _ensure_task_final_report(
            store, "run-1", {}, "task-1",
            {"result": "FAILED", "terminal_step": "run_regression", "terminal_reason": "第一次的报错"},
            finished_at="2026-01-01T00:03:00", error=None,
        )
        self.assertEqual(report["result"], "SUCCESS")
        self.assertNotIn("第一次的报错", report["terminal_reason"])
        self.assertEqual(report["terminal_reason"], finalize_output["terminal_reason"])
        self.assertEqual(report["terminal_step"], "publish_delivery")


class LegacyReportHelperTests(unittest.TestCase):
    """旧框架证据、报告路径与修改范围判定保持一致。"""

    def test_legacy_reference_and_report_framework(self):
        from app.workflow.steps.prepare_delivery.report_paths import (
            is_legacy_changed_path,
            legacy_reference,
            report_dir,
            report_framework,
        )

        locate = {
            "source_evidence": [{"path": "spiders/Test/main.py"}],
            "candidate_files": ["gathers/Test/a.py"],
        }
        self.assertTrue(legacy_reference(locate))
        self.assertTrue(legacy_reference({"candidate_files": ["spiders/Test/a.py"]}))
        self.assertFalse(legacy_reference({"candidate_files": ["gathers/Test/a.py"]}))
        self.assertFalse(legacy_reference(None))

        self.assertEqual(
            report_framework({"legacy_only": True}, None, "gathers"), "spiders"
        )
        self.assertEqual(report_framework({}, locate, "gathers"), "spiders")
        self.assertEqual(report_framework({}, {}, "gathers"), "gathers")

        self.assertEqual(
            report_dir({"carrier_dir": "gathers\\Test"}, "gathers", "test"),
            "gathers/Test",
        )
        self.assertEqual(report_dir({}, "spiders", "test"), "spiders/test")

        self.assertTrue(is_legacy_changed_path("spiders\\Test\\main.py"))
        self.assertTrue(is_legacy_changed_path("SPIDERS/Test/main.py"))
        self.assertFalse(is_legacy_changed_path("gathers/Test/main.py"))
