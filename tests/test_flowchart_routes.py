import unittest
from workflow.engine import routes as r

class FlowchartRouteTests(unittest.TestCase):
    def test_main_flow(self):
        passed = {"last_result": "passed"}
        self.assertEqual(r.after_prepare_context(passed), r.CLAIM)
        self.assertEqual(r.after_claim(passed), r.UNIFIED_FIXER)
        self.assertEqual(r.after_unified_fixer({**passed, "changed_files": ["main.py"]}), r.REVIEW)
        self.assertEqual(r.after_report(passed), r.SUBMIT)
        self.assertEqual(r.after_submit(passed), r.APPROVE)
        self.assertEqual(r.after_approval(passed), r.FINALIZE)

    def test_analysis_skips_review(self):
        self.assertEqual(r.after_unified_fixer({"last_result": "passed", "analysis_only": True}), r.REPORT)
        self.assertEqual([d.name for d in r.dependencies_for(r.REPORT)], [r.UNIFIED_FIXER])

    def test_claim_conflict_finalizes(self):
        self.assertEqual(r.after_claim({"last_result": "manual"}), r.FINALIZE)
        self.assertEqual(r.dependencies_for(r.FINALIZE), ())

    def test_cancel_finalizes_without_delivery(self):
        for route in (r.after_prepare_context, r.after_claim, r.after_unified_fixer, r.after_review, r.after_report, r.after_submit):
            self.assertEqual(route({"last_result": "cancelled"}), r.FINALIZE)

    def test_approval_cleanup_and_exhausted_retry_finalize(self):
        self.assertEqual(r.after_approval({"last_result": "passed", "decision": "APPROVE"}), r.FINALIZE)
        self.assertEqual(r.after_approval({"last_result": "cancelled"}), r.FINALIZE)
        self.assertEqual(r.after_approval({"approval_cleanup_failed": True}), r.FINALIZE)
        self.assertEqual(r.after_approval({"last_result": "rejected", "decision": "REJECT", "attempt": 3, "max_attempts": 3}), r.FINALIZE)
