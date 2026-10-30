import unittest

from server.view_models import delivery_view


class DeliveryViewTests(unittest.TestCase):
    def test_analysis_only_metadata_survives_publish(self):
        view = delivery_view([
            {
                'name': 'generate_report', 'status': 'succeeded',
                'output': {
                    'candidate_id': 'candidate-1', 'commit': 'abc123', 'repair_branch': 'repair/test',
                    'analysis_only': True, 'report_paths': ['gathers/TEST/final.json'],
                    'local_branch': True,
                },
            },
            {
                'name': 'submit_changes', 'status': 'succeeded',
                'output': {'candidate_id': 'candidate-1', 'commit': 'abc123', 'repair_branch': 'repair/test'},
            },
        ])
        self.assertEqual(view.phase, 'published')
        self.assertTrue(view.analysis_only)
        self.assertEqual(view.report_paths, ['gathers/TEST/final.json'])
        self.assertTrue(view.local_branch)

    def test_failed_submit_keeps_delivery_failure_and_analysis_identity(self):
        view = delivery_view([
            {
                'name': 'generate_report', 'status': 'succeeded',
                'output': {'candidate_id': 'candidate-2', 'analysis_only': True, 'report_paths': ['gathers/TEST/final.json']},
            },
            {'name': 'submit_changes', 'status': 'failed', 'output': {}},
        ])
        self.assertEqual(view.phase, 'failed')
        self.assertTrue(view.analysis_only)
        self.assertEqual(view.report_paths, ['gathers/TEST/final.json'])


if __name__ == '__main__':
    unittest.main()
