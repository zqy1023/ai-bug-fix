"""派单选择回归：每组每轮一个名额，给窗口内出现次数最多的异常。"""

import unittest

from workflow.stages.log_pipeline.identify_carrier.node import (
    select_representative,
    task_problem,
)


def _problem(index, count, error, **extra):
    record = {
        "representative_index": index,
        "occurrence_count": count,
        "problem": {
            "service_name": "gather.py.track",
            "biztype": "framework.track_server",
            "carrier": "Unitrade",
            "req": '{"carrier":"Unitrade"}',
            "error": error,
            "raw_error": error,
        },
    }
    record["problem"].update(extra)
    return record


class SelectRepresentativeTests(unittest.TestCase):
    def test_most_frequent_wins_over_earliest(self):
        """449 次的系统性问题优先于 12 次的长尾，即使后者更早出现。"""
        chosen = select_representative([
            _problem(0, 12, "builtins.KeyError, missing key"),
            _problem(5, 449, "builtins.IndexError, list index out of range"),
        ])
        self.assertEqual(chosen["occurrence_count"], 449)
        self.assertIn("IndexError", chosen["problem"]["error"])

    def test_tie_falls_back_to_earliest(self):
        chosen = select_representative([
            _problem(9, 30, "builtins.KeyError, b"),
            _problem(3, 30, "builtins.KeyError, a"),
        ])
        self.assertEqual(chosen["representative_index"], 3)

    def test_single_candidate(self):
        only = _problem(0, 1, "builtins.IndexError, x")
        self.assertIs(select_representative([only]), only)


class TaskProblemTests(unittest.TestCase):
    def test_passes_through_structured_classification(self):
        problem = task_problem(_problem(
            0, 5, "builtins.IndexError, list index out of range",
            message_format="req",
            exception_class="builtins.IndexError",
            frame="tpcindia.py:41:read_html",
            blame="code",
            response_kind="json",
            response='{"ok":false}',
            track_config='{"timeout":60000}',
            message="raw log line",
        ))
        self.assertEqual(problem["exception_class"], "builtins.IndexError")
        self.assertEqual(problem["frame"], "tpcindia.py:41:read_html")
        self.assertEqual(problem["blame"], "code")
        self.assertEqual(problem["message_format"], "req")
        self.assertEqual(problem["response"], '{"ok":false}')
        self.assertEqual(problem["track_config"], '{"timeout":60000}')
        self.assertEqual(problem["message"], "raw log line")

    def test_drops_empty_optional_fields(self):
        problem = task_problem(_problem(0, 5, "builtins.KeyError, x", frame=""))
        self.assertNotIn("frame", problem)
        self.assertEqual(
            set(problem),
            {"service_name", "biztype", "carrier", "req", "error", "raw_error"},
        )

    def test_business_records_are_not_excluded_here(self):
        """业务结果只在清洗阶段打标，派单这里不擅自过滤：404 也可能是本地解析缺陷。"""
        problem = task_problem(_problem(
            0, 3, "requestResponse: code=404, error_type=ProcessError",
            message_format="requestResponse", blame="business",
        ))
        self.assertEqual(problem["blame"], "business")


if __name__ == "__main__":
    unittest.main()
