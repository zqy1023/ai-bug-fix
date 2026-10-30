import unittest
from workflow.engine import routes as r


class FlowchartRouteTests(unittest.TestCase):
    def test_main_flow(self):
        passed = {"last_result": "passed"}
        self.assertEqual(r.after_prepare_context(passed), r.CLAIM)
        self.assertEqual(r.after_claim(passed), r.UNIFIED_FIXER)
        self.assertEqual(r.after_unified_fixer({**passed, "changed_files": ["main.py"]}), r.REVIEW)
        self.assertEqual(r.after_report(passed), r.SUBMIT)
        self.assertEqual(r.after_submit(passed), r.FINALIZE)

    def test_analysis_requires_independent_review(self):
        self.assertEqual(r.after_unified_fixer({"last_result": "passed", "analysis_only": True}), r.REVIEW)
        self.assertEqual(r.after_unified_fixer({"last_result": "passed", "changed_files": []}), r.REVIEW)
        self.assertEqual(r.after_unified_fixer({"last_result": "passed", "evidence": {"framework": "spiders"}}), r.REVIEW)
        self.assertEqual([d.name for d in r.dependencies_for(r.REPORT)], [r.UNIFIED_FIXER])

    def test_duplicate_branch_skips_without_delivery(self):
        self.assertEqual(r.after_prepare_context({"last_result": "skipped"}), r.FINALIZE)
        self.assertEqual(r.after_claim({"last_result": "skipped"}), r.FINALIZE)
        self.assertEqual(r.dependencies_for(r.FINALIZE), ())

    def test_cancel_finalizes_without_delivery(self):
        for route in (r.after_prepare_context, r.after_claim, r.after_unified_fixer, r.after_review, r.after_report, r.after_submit):
            self.assertEqual(route({"last_result": "cancelled"}), r.FINALIZE)

    def test_review_failure_uses_configured_limit_then_fails(self):
        first = {"last_result": "failed", "automatic_attempt": 1, "review": {"verdict": "FAIL"}, "agent_policy": {"automatic_repair": True}}
        self.assertEqual(r.after_review(first), r.RETRY)
        second = {"last_result": "failed", "automatic_attempt": 4, "review": {"verdict": "FAIL"}, "agent_policy": {"automatic_repair": True, "max_repairs": 3}}
        self.assertEqual(r.after_review(second), r.FINALIZE)
        self.assertFalse(second.get("analysis_only"))

    def test_fixer_failure_is_not_published_as_analysis_delivery(self):
        self.assertEqual(r.after_unified_fixer({"last_result": "failed"}), r.FINALIZE)
        self.assertEqual(r.after_prepare_context({"last_result": "failed"}), r.FINALIZE)
        self.assertEqual(r.after_claim({"last_result": "failed"}), r.FINALIZE)
