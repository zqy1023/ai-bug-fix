import importlib
import unittest

from infrastructure.es.logs import ESLogAdapter

normalize = importlib.import_module('workflow.stages.00_log_pipeline.normalize_error.node')._normalize
consume = importlib.import_module('workflow.stages.00_log_pipeline.deduplicate_error.node').consume


class OriginalMessageTests(unittest.TestCase):
    def test_es_to_deduplicated_problem_preserves_full_message(self):
        message = ('  request context\n' + 'stack frame\n' * 120
                   + 'req:{"carrier":"TEST","number":"001"} error: ValueError: invalid value\n  ')
        record = ESLogAdapter.parse_hit({'_source': {
            'service_name': 'test-service', 'biztype': 'track', 'message': message,
        }})
        self.assertIsNotNone(record)
        self.assertEqual(record['message'], message)
        groups = {}
        consume(groups, normalize(record, 1), ())
        problem = next(iter(groups.values()))['problem']
        self.assertEqual(problem['message'], message)
        self.assertNotEqual(problem['error'], message)


if __name__ == '__main__':
    unittest.main()
