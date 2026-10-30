"""交付说明应区分建议、真实改动及终态，防止误导人工接手。"""

import unittest

from infrastructure.git.task_summary import render_task_summary


class TaskSummaryTests(unittest.TestCase):
    def test_review_todo_is_retained_as_nonblocking_suggestions(self):
        report = {"result": "SUCCESS", "changed_files": ["gathers/T/main.py"],
                  "review": {"todo": ["补充多文本节点回归"]}}
        markdown = render_task_summary(report)
        self.assertIn("## TODO（非阻断回归建议）\n\n- [ ] 补充多文本节点回归", markdown)
        self.assertNotIn("需人工确认", markdown)
        report.update(result="ANALYSIS_ONLY", analysis_only=True)
        self.assertIn("## TODO（非阻断回归建议）", render_task_summary(report))

    def test_legacy_todo_contains_conclusion_and_action_without_merge(self):
        markdown = render_task_summary({
            "result": "MANUAL", "locate_status": "ANALYSIS_ONLY",
            "candidate_files": ["spiders/TEST/main.py"],
            "root_cause": "空列表直接取下标",
            "change_plan": {"edits": [{
                "path": "spiders/TEST/main.py", "symbol": "parse",
                "operation": "增加空列表分支", "validation_notes": "重放空结果样本",
            }]},
            "manual_confirmation_required": True,
            "review_decision": "MANUAL",
            "acceptance_criteria": ["空结果不抛出 IndexError"],
        })
        self.assertIn("# 旧框架 · 分析交付", markdown)
        todo = markdown.split("## TODO（需人工确认）")[1]
        self.assertIn("分析结论：空列表直接取下标", todo)
        self.assertIn("增加空列表分支", todo)
        self.assertIn("重放空结果样本", todo)
        self.assertIn("验收：空结果不抛出 IndexError", todo)
        self.assertNotIn("合并", markdown)
        self.assertNotIn("## 已实施修改", markdown)

    def test_failed_delivery_keeps_failure_ahead_of_legacy_analysis(self):
        markdown = render_task_summary({
            "result": "FAILED", "candidate_files": ["spiders/TEST/main.py"],
            "terminal_reason": "push failed", "root_cause": "空列表直接取下标",
        })
        self.assertTrue(markdown.startswith("# 执行失败"))
        self.assertIn("push failed", markdown)
        self.assertNotIn("# 旧框架 · 分析交付", markdown)

    def test_review_snapshot_does_not_claim_delivery_success(self):
        markdown = render_task_summary({
            "report_kind": "repair_review", "result": "PASS",
            "changed_files": ["gathers/TEST/main.py"],
            "review": {"dimensions": {"business": {"status": "unverified"}}},
        })
        self.assertTrue(markdown.startswith("# 修复已审查 · 待复核交付"))
        self.assertIn("## 已实施修改", markdown)
        self.assertIn("业务未验证", markdown)
        self.assertNotIn("修复交付完成", markdown)

    def test_missing_analysis_does_not_invent_fix_and_preserves_error_fence(self):
        markdown = render_task_summary({
            "result": "ANALYSIS_ONLY", "problem": {"error": "before\n```\nafter"},
        })
        self.assertIn("尚未形成根因结论", markdown)
        self.assertIn("尚无可执行的修改方案", markdown)
        self.assertIn("````text\nbefore\n```\nafter\n````", markdown)

    def test_completed_and_running_tasks_have_different_sections(self):
        completed = render_task_summary({"result": "SUCCESS", "changed_files": ["gathers/T/main.py"]})
        running = render_task_summary({"status": "RUNNING"})
        self.assertTrue(completed.startswith("# 修复交付完成"))
        self.assertIn("## 验证与审查", completed)
        self.assertTrue(running.startswith("# 任务进行中"))
        self.assertIn("## 当前进度与阻塞", running)
        self.assertNotIn("## 验证与审查", running)


if __name__ == "__main__":
    unittest.main()
