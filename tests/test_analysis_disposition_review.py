"""Mixed-framework carriers retain local repair rights and report-only decisions are reviewed."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest

from agents.review import SPEC
from core.policy import legacy_analysis_required
from infrastructure.storage.store import RecordStore
from workflow.engine.graph import build_agent_node
from workflow.engine import routes
from workflow.stages.prepare_context.prepare_evidence.node import _references_legacy
from workflow.stages.generate_report.report_paths import report_framework


def test_replay_rejects_wrong_interpreter_before_any_mutation(tmp_path):
    from docs.rerun_affected_tasks import execute
    with patch('sys.prefix', str(tmp_path)), patch('docs.rerun_affected_tasks.parent_status') as parent:
        with pytest.raises(RuntimeError, match='项目解释器'):
            execute({}, tmp_path)
    parent.assert_not_called()


def test_legacy_reference_is_evidence_not_a_carrier_write_ban(tmp_path):
    new, old = tmp_path / 'gathers/TEST', tmp_path / 'spiders/TEST'
    new.mkdir(parents=True)
    old.mkdir(parents=True)
    entry = new / 'main.py'
    entry.write_text('# from spiders.TEST import LegacySpiderGather\nlabel = "spiders.TEST"\n')
    assert not _references_legacy(new, old)
    entry.write_text('from gather_providers.legacy_spider_provider import LegacySpiderProvider\n')
    assert _references_legacy(new, old)
    evidence = {'framework': 'gathers', 'legacy_dependency': True}
    assert not legacy_analysis_required({'evidence': evidence})
    assert legacy_analysis_required({'evidence': {'framework': 'spiders'}})
    assert legacy_analysis_required({'evidence': evidence, 'changed_files': ['spiders/TEST/main.py']})
    assert report_framework(evidence, {'candidate_files': ['spiders/TEST/main.py']}, 'gathers') == 'gathers'


def test_mixed_carrier_gets_writable_repair_agent(tmp_path):
    from agents.unified_fixer.graph import run_unified_fixer
    from agents.unified_fixer.models import UnifiedFixerConfig
    root = tmp_path / 'gathers/TEST'
    root.mkdir(parents=True)
    target = root / 'main.py'
    target.write_text('value = 1\n')
    calls = []

    def runner(**kwargs):
        calls.append(kwargs)
        target.write_text('value = 2\n')
        return {'result': json.dumps({'status': 'FIXED', 'changed_files': ['gathers/TEST/main.py']})}

    state = {'run_id': 'r', 'task_id': 't', 'attempt': 1, 'carrier': 'TEST',
             'workspace_path': str(tmp_path), 'evidence': {'framework': 'gathers', 'legacy_dependency': True},
             'problem': {'service_name': 'test', 'biztype': 'track', 'carrier': 'TEST', 'exception_class': 'TypeError'}}
    with patch('agents.unified_fixer.provider.invoke_with_audit', side_effect=lambda request, invoke: invoke()):
        result = run_unified_fixer(state, SimpleNamespace(config=UnifiedFixerConfig(), agent_runner=runner))
    assert calls[0]['writable']
    assert result['changed_files'] == ['gathers/TEST/main.py']
    assert result['status'] == 'FIXED'


def test_unmodified_analysis_reaches_review_and_can_require_local_repair(tmp_path):
    workspace, baseline = tmp_path / 'workspace', tmp_path / 'baseline'
    for root in (workspace, baseline):
        source = root / 'gathers/TEST/main.py'
        source.parent.mkdir(parents=True)
        source.write_text('import json\ndef parse(data):\n    return json.loads(data.get("json", {}))\n')
    store = RecordStore(tmp_path / 'runtime')
    evidence = {'framework': 'gathers', 'legacy_dependency': True}
    state = {'run_id': 'r', 'task_id': 't', 'attempt': 1,
             'task': {'carrier': 'TEST', 'biztype': 'track', 'service_name': 'test'},
             'framework': 'gathers', 'evidence': evidence, 'ai_workspace_path': str(workspace),
             'baseline_dir': str(baseline), 'changed_files': [], 'analysis_only': True,
             'last_result': 'passed', 'agent_policy': {'automatic_repair': True},
             'problem': {'carrier': 'TEST', 'service_name': 'test', 'biztype': 'track'},
             'unified_fixer': {'status': 'ANALYSIS_ONLY', 'root_cause': 'dict passed to json.loads',
                               'blocked_reason': '旧依赖任务只读'}}
    assert routes.after_unified_fixer(state) == routes.REVIEW
    requests = []

    def invoke(request):
        requests.append(request)
        return {'result': json.dumps({
            'verdict': 'FAIL', 'passed': False, 'code_verdict': 'FAIL',
            'coverage': {'decision': 'sufficient', 'reason': '核对实际入口与局部解析',
                         'reviewed_symbols': ['gathers/TEST/main.py:parse'], 'missing_checks': []},
            'dimensions': {key: {'status': 'failed' if key == 'business' else 'passed',
                                 'reason': '类型缺陷仍存在' if key == 'business' else '无新增改动',
                                 'evidence': ['gathers/TEST/main.py:3']}
                           for key in ('syntax', 'business', 'scope')},
            'summary': '旧引用不阻止独立解析修复', 'findings': ['明确类型缺陷未处理'],
            'required_changes': ['按现有错误协议处理异常结构，保留正常解析'], 'todo': [],
        })}

    with patch('infrastructure.workspace.validation.run_validation') as validate:
        result = build_agent_node(SPEC, store, runtime=SimpleNamespace(run=invoke))(state)
    validate.assert_not_called()
    assert len(requests) == 1
    assert 'analysis_disposition' in requests[0].prompt
    assert '旧依赖任务只读' in requests[0].prompt
    assert result['review']['code_verdict'] == 'FAIL'
    merged = {**state, **result}
    assert routes.after_review(merged) == routes.RETRY
    assert merged['retry_target'] == routes.UNIFIED_FIXER


def test_no_change_delivery_can_pass_review_without_claiming_a_fix(tmp_path):
    from agents.review.graph import review
    root = tmp_path / 'gathers/TEST'
    root.mkdir(parents=True)
    (root / 'main.py').write_text('value = 1\n')
    payload = {
        'verdict': 'PASS', 'passed': True, 'code_verdict': 'PASS',
        'coverage': {'decision': 'sufficient', 'reason': '上游故障无需本地改动',
                     'reviewed_symbols': ['gathers/TEST/main.py'], 'missing_checks': []},
        'dimensions': {key: {'status': 'passed', 'reason': '不修改的处置合理',
                             'evidence': ['gathers/TEST/main.py']} for key in ('syntax', 'business', 'scope')},
        'summary': '批准分析交付；没有源码修复', 'findings': [], 'required_changes': [], 'todo': [],
    }
    result = review({'run_id': 'r', 'task_id': 't', 'carrier': 'TEST', 'framework': 'gathers',
                     'ai_workspace_path': str(tmp_path), 'changed_files': [], 'attempt': 1,
                     'problem': {'service_name': 'test', 'biztype': 'track', 'carrier': 'TEST'}},
                    SimpleNamespace(run=lambda request: {'result': json.dumps(payload)}))
    assert result['verdict'] == 'PASS'
    assert result['validation']['replay']['status'] == 'unverified'
