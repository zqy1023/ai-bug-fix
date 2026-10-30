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
        'findings': [], 'required_changes': [], 'todo': [], 'summary': '静态源码审查通过',
    }


class AgentReviewFlowTests(unittest.TestCase):
    def test_code_pass_with_required_changes_is_a_protocol_error(self):
        from agents.review.contract import delivery_allowed, judge
        from workflow.engine.routes import after_review, RETRY
        result = verdict()
        result.update(verdict="MANUAL", passed=False,
                      required_changes=["决策事项：确认空值接受度后再交付"])
        result["dimensions"]["business"]["status"] = "unverified"
        checked = judge(result)
        self.assertFalse(checked.valid)
        self.assertIn("PASS", checked.reason)
        self.assertFalse(delivery_allowed(result))
        result["contract_error"] = "review_protocol_invalid:" + checked.reason
        state = {"review": result, "attempt": 1}
        self.assertEqual(after_review(state), RETRY)
        self.assertEqual(state["retry_target"], "review_agent")

    def test_real_semantic_blocker_is_unknown_and_blocks_delivery(self):
        from agents.review.contract import delivery_allowed, judge
        result = verdict()
        result.update(verdict="MANUAL", passed=False, code_verdict="UNKNOWN",
                      required_changes=["必须确认缺失字段是否允许缺省"])
        result["coverage"].update(decision="unknown", missing_checks=["字段缺省契约"])
        result["dimensions"]["business"]["status"] = "unverified"
        self.assertTrue(judge(result).valid)
        self.assertFalse(delivery_allowed(result))

    def test_code_pass_cannot_bypass_missing_coverage_via_manual_verdict(self):
        from agents.review.contract import delivery_allowed, judge
        for decision in ("unknown", "insufficient"):
            result = verdict()
            result.update(verdict="MANUAL", passed=False)
            result["coverage"].update(decision=decision, missing_checks=["需确认新增条件不会误判正常响应"])
            result["dimensions"]["business"]["status"] = "unverified"
            self.assertFalse(judge(result).valid)
            self.assertFalse(delivery_allowed(result))

    def test_runtime_unverified_only_can_preserve_complete_code_review(self):
        from agents.review.contract import delivery_allowed, judge
        result = verdict()
        result.update(verdict="MANUAL", passed=False)
        result["dimensions"]["business"]["status"] = "unverified"
        result["todo"] = ["后续增加正常单号回归"]
        self.assertTrue(judge(result).valid)
        self.assertFalse(delivery_allowed(result))  # Unverified semantics cannot be waived as runtime-only.
        result["semantic_verified"] = True
        self.assertTrue(delivery_allowed(result))

    def test_todo_is_retained_without_overriding_a_real_blocker(self):
        from agents.review.contract import delivery_allowed, judge, slim_review
        result = verdict()
        result["todo"] = ["后续补充多文本节点回归"]
        self.assertTrue(judge(result).valid)
        self.assertTrue(delivery_allowed(result))
        self.assertEqual(slim_review(result)["todo"], result["todo"])
        result.update(verdict="FAIL", passed=False, code_verdict="FAIL",
                      findings=["首项取值被改为合并全部结果"], required_changes=["恢复正常输入的原取值规则"])
        result["dimensions"]["scope"]["status"] = "failed"
        self.assertTrue(judge(result).valid)
        self.assertFalse(delivery_allowed(result))

    def test_old_artifact_fields_are_not_restored_into_current_state(self):
        from core.contracts import TaskArtifacts
        artifacts = TaskArtifacts.model_validate({'gate_result': {'passed': True},
                                                  'validation_plan': {'obsolete': True},
                                                  'review': verdict()})
        current = artifacts.model_dump()
        self.assertNotIn('gate_result', current)
        self.assertNotIn('validation_plan', current)
        self.assertEqual(current['review']['verdict'], 'PASS')

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
                payload = verdict()
                payload['todo'] = [f'非阻断回归建议 {index}' for index in range(10)]
                return {'result': json.dumps(payload)}

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
            with patch.dict('sys.modules', {'agents.review.gates': None}):
                result = build_agent_node(SPEC, store, runtime=runtime)(state)
            self.assertEqual(len(requests), 1)
            self.assertEqual(result['review']['verdict'], 'MANUAL')
            self.assertEqual(result['review']['dimensions']['business']['status'], 'unverified')
            self.assertEqual(result['review']['code_verdict'], 'PASS')
            self.assertEqual(result['review']['todo'], [f'非阻断回归建议 {index}' for index in range(10)])
            self.assertEqual([step.name for step in store.list_steps('run', 'task')], ['review_agent'])
            self.assertNotIn('substeps', result['review'])
            self.assertEqual(result['review']['diff_files'][0]['path'], 'gathers/TEST/main.py')
            diff = store.read_artifact('run', 'artifacts/diff.json', 'task')
            self.assertEqual(diff['files'][0]['before'], (baseline / 'gathers/TEST/main.py').read_bytes().decode('utf-8'))
            self.assertEqual(diff['files'][0]['after'], (workspace / 'gathers/TEST/main.py').read_bytes().decode('utf-8'))
            self.assertEqual(result['review']['review_contract_version'], REVIEW_CONTRACT_VERSION)
            combined = {**state, **result}
            data = _input(combined)
            self.assertEqual(data['binding']['revision_id'], result['review']['evidence_version']['patch_id'])
            (workspace / 'gathers/TEST/main.py').write_text('value = 3\n', encoding='utf-8')
            with self.assertRaises(StepFailed):
                _input(combined)

    def test_approval_checks_version_without_running_review_again(self):
        from workflow.engine.handoff import evidence_version
        from workflow.stages.request_approval.approval import final_gate
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'main.py'
            target.write_text('value = 1\n')
            state = {'candidate_id': 'candidate', 'ai_workspace_path': directory,
                     'changed_files': ['main.py'], 'baseline_dir': '',
                     'evidence_version': evidence_version(directory, ['main.py'])}
            self.assertTrue(final_gate(state)['passed'])
            target.write_text('value = 2\n')
            self.assertFalse(final_gate(state)['passed'])

    def test_run_projection_only_reads_primary_steps(self):
        from core.contracts import StepRecord
        from server.run_service import _run_payload
        with tempfile.TemporaryDirectory() as directory:
            store = RecordStore(directory)
            store.save('run', StepRecord(name='review_agent', title='Agent review', status='succeeded'), 'task')
            (store.run_dir('run') / 'run.json').write_text(json.dumps({'workflow_version': WORKFLOW_VERSION}))
            result = _run_payload(store, 'run')
            self.assertEqual([step['name'] for step in result['tasks'][0]['steps']], ['review_agent'])

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
