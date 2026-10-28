import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agents.review import SPEC
from agents.review.contract import REVIEW_CONTRACT_VERSION
from core.contracts import WORKFLOW_VERSION
from core.errors import StepFailed
from infrastructure.storage.store import RecordStore
from workflow.engine.graph import build_agent_node
from workflow.stages.generate_report import _input


def verdict():
    return {
        'verdict': 'PASS', 'passed': True, 'code_verdict': 'PASS',
        'coverage': {'decision': 'sufficient', 'reason': '读取基线和修改后的分支',
                     'reviewed_symbols': ['gathers/TEST/main.py:value'], 'missing_checks': []},
        'dimensions': {key: {'status': 'passed', 'reason': '已核对源码与基线',
                             'evidence': ['gathers/TEST/main.py:1']}
                       for key in ('syntax', 'business', 'scope')},
        'findings': [], 'required_changes': [], 'summary': '静态源码审查通过',
    }


class AgentReviewFlowTests(unittest.TestCase):
    def test_agent_runs_without_legacy_scripts_and_delivery_binds_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, baseline = root / 'workspace', root / 'baseline'
            for base, value in ((workspace, 2), (baseline, 1)):
                target = base / 'gathers/TEST/main.py'
                target.parent.mkdir(parents=True)
                target.write_text(f'value = {value}\n', encoding='utf-8')
            store = RecordStore(root / 'runtime')
            requests = []

            def invoke(request):
                requests.append(request)
                return {'result': json.dumps(verdict())}

            runtime = SimpleNamespace(run=invoke)
            state = {
                'run_id': 'run', 'task_id': 'task', 'attempt': 1,
                'task': {'carrier': 'TEST', 'biztype': 'track', 'service_name': 'test'},
                'framework': 'gathers', 'ai_workspace_path': str(workspace),
                'baseline_dir': str(baseline), 'changed_files': ['gathers/TEST/main.py'],
                'problem': {'carrier': 'TEST', 'biztype': 'track', 'service_name': 'test', 'error': 'value incorrect'},
                # 历史脚本失败结果不能替代当前 Agent 的判断。
                'gate_result': {'passed': False, 'evidence_version': {'patch_id': 'old'}},
                'syntax_result': {'passed': False},
            }
            with patch('agents.review.gates.registry.resolve_tool', side_effect=AssertionError('旧脚本被调用')):
                result = build_agent_node(SPEC, store, runtime=runtime)(state)
            self.assertEqual(len(requests), 1)
            self.assertEqual(result['review']['verdict'], 'PASS')
            self.assertTrue(all(row['status'] == 'passed' for row in result['review']['checks']))
            self.assertEqual([step.name for step in store.list_steps('run', 'task')], ['review_agent'])
            self.assertEqual(result['review']['substeps'], [])
            self.assertEqual(result['review']['review_contract_version'], REVIEW_CONTRACT_VERSION)
            combined = {**state, **result}
            data = _input(combined)
            self.assertEqual(data['binding']['revision_id'], result['review']['evidence_version']['patch_id'])
            (workspace / 'gathers/TEST/main.py').write_text('value = 3\n', encoding='utf-8')
            with self.assertRaises(StepFailed):
                _input(combined)

    def test_source_change_during_agent_call_invalidates_review(self):
        from agents.review.graph import review
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'gathers/TEST/main.py'
            target.parent.mkdir(parents=True)
            target.write_text('value = 1\n')

            def invoke(request):
                target.write_text('value = 2\n')
                return {'result': json.dumps(verdict())}

            result = review({'run_id': 'run', 'task_id': 'task', 'carrier': 'TEST',
                             'framework': 'gathers', 'ai_workspace_path': directory,
                             'attempt': 1, 'problem': {'service_name': 'test', 'biztype': 'track', 'carrier': 'TEST'}, 'changed_files': ['gathers/TEST/main.py']},
                            SimpleNamespace(run=invoke))
            self.assertFalse(result['passed'])
            self.assertEqual(result['_audit']['blocked_by'], 'source_changed_during_review')

    def test_published_analysis_report_can_be_confirmed(self):
        from server.routes import approvals
        for version, enabled in ((WORKFLOW_VERSION, True), ('old', False)):
            with self.subTest(version=version):
                store = Mock()
                store.load_run.return_value = {'workflow_version': version}
                steps = [{'name': 'generate_report'}, {'name': 'submit_changes'}]
                candidate = SimpleNamespace(phase='published', candidate_id='candidate',
                                            model_dump=lambda **kwargs: {'phase': 'published'})
                pending = {'items': [{'run_id': 'run', 'task_id': 'task', 'approval_step': {}}]}
                with patch.object(approvals, '_pending_approvals', return_value=pending), \
                     patch.object(approvals, '_store', return_value=store), \
                     patch.object(approvals, '_run_payload', return_value={'tasks': [{'task_id': 'task', 'steps': steps}]}), \
                     patch.object(approvals, '_formal_step_payload', side_effect=lambda *args: args[-1]), \
                     patch.object(approvals, 'delivery_view', return_value=candidate):
                    result = approvals.v1_list_approvals(status='pending')
                self.assertEqual(result['items'][0]['actions']['approve']['enabled'], enabled)


if __name__ == '__main__':
    unittest.main()
