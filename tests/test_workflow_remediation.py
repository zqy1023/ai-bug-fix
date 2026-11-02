"""真实执行边界、跨资源故障补偿和最终审批的隔离回归。"""
import ast
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from core.contracts import StepSpec, WORKFLOW_VERSION
from core.acceptance import freeze_acceptance
from infrastructure.storage.store import RecordStore
from infrastructure.storage.lifecycle import LifecycleStore
from infrastructure.storage.retry import RetryOperation, recover_retry
from infrastructure.storage.reports import archive_retry_reports
from infrastructure.storage.final_approval import register_approval, decide_approval
from workflow.engine.runner import run_stage, WorkflowServices


def test_real_stage_executes_and_persists_success(tmp_path):
    class Input(BaseModel):
        value: int

    class Output(BaseModel):
        result: str

    store = RecordStore(tmp_path)
    store.create_run('run', {'workflow_version': WORKFLOW_VERSION})
    spec = StepSpec(name='audit_stage', title='probe', input_model=Input, output_model=Output,
                    input_builder=lambda state: {'value': 3}, runner=lambda ctx, data: Output(result='passed'), task_level=False)
    run_stage(spec, store, WorkflowServices(), {'run_id': 'run'})
    assert store.load('run', spec.name).status == 'succeeded'


def test_global_execution_slot_blocks_other_run_until_release(tmp_path):
    first, second = LifecycleStore(tmp_path), LifecycleStore(tmp_path)
    assert first.claim_worker_lease('a', owner_id='worker-a', lease_token='token-a')
    assert not second.claim_worker_lease('b', owner_id='worker-b', lease_token='token-b')
    assert first.release_worker_lease('a', owner_id='worker-a', lease_token='token-a')
    assert second.claim_worker_lease('b', owner_id='worker-b', lease_token='token-b')
    assert not first.release_worker_lease('a', owner_id='worker-a', lease_token='token-a')
    assert second.owns_worker_lease('b', owner_id='worker-b', lease_token='token-b')


def test_held_run_lock_never_changes_identity():
    from server import context
    from collections import OrderedDict
    with patch.object(context, '_RUN_LOCKS', OrderedDict()), patch.object(context, '_RUN_LOCKS_LIMIT', 1):
        first = context._run_lock('a')
        first.acquire()
        try:
            context._run_lock('b')
            assert context._run_lock('a') is first
        finally:
            first.release()


def prepared_retry(tmp_path):
    store = RecordStore(tmp_path)
    store.create_run('run', {'workflow_version': WORKFLOW_VERSION})
    store.write_artifact('run', 'reports/final.json', {'attempt': 1, 'result': 'FAILED'}, 'task')
    life = LifecycleStore(tmp_path)
    life.update_run('run', status='failed')
    life.update_task('run', 'task', status='failed')
    operation = RetryOperation(store, 'run', 'task')
    archive_retry_reports(store, 'run', 'task')
    return store, operation


def test_failed_checkpoint_restores_report_and_lifecycle(tmp_path):
    store, operation = prepared_retry(tmp_path)
    graph = SimpleNamespace(update_state=lambda *a, **k: (_ for _ in ()).throw(OSError('checkpoint unavailable')),
                            get_state=lambda cfg: SimpleNamespace(values={}))
    with pytest.raises(OSError):
        operation.checkpoint(graph, {}, {'tasks': ['new']}, as_node='dispatch')
    operation.rollback()
    assert store.read_artifact('run', 'reports/final.json', 'task')['attempt'] == 1
    assert LifecycleStore(tmp_path).read_task('run', 'task')['status'] == 'failed'
    assert store.database('run').get('', 'retry_intent')['phase'] == 'rolled_back'


def test_lost_checkpoint_receipt_recovers_without_duplicate_dispatch(tmp_path):
    store, operation = prepared_retry(tmp_path)
    state, writes = {}, []

    def update(cfg, patch, **kw):
        state.update(patch)
        writes.append(patch)
        raise OSError('response lost after commit')

    graph = SimpleNamespace(update_state=update, get_state=lambda cfg: SimpleNamespace(values=state))
    with pytest.raises(OSError):
        operation.checkpoint(graph, {}, {'tasks': ['original', 'retry']}, as_node='dispatch')
    operation.rollback()
    recover_retry(store, 'run', graph, {})
    assert len(writes) == 1
    assert state['tasks'] == ['original', 'retry']
    assert store.database('run').get('', 'retry_intent')['phase'] == 'queued'


def test_crash_during_preparation_restores_moved_workspace(tmp_path):
    store, operation = prepared_retry(tmp_path)
    source = store.task_dir('run', 'task') / 'source_snapshot'
    source.mkdir()
    (source / 'main.py').write_text('original')
    target = source.with_name('source.previous')
    operation.moving(source, target)
    source.replace(target)
    recover_retry(store, 'run', SimpleNamespace(), {})
    assert (source / 'main.py').read_text() == 'original'
    assert store.read_artifact('run', 'reports/final.json', 'task')['attempt'] == 1


def test_final_approval_is_bound_idempotent_and_never_merges(tmp_path):
    store = RecordStore(tmp_path)
    delivery = {'candidate_id': 'candidate-1', 'commit': 'a' * 40, 'repair_branch': 'fix/test'}
    register_approval(store, 'run', 'task', delivery)
    with pytest.raises(ValueError):
        decide_approval(store, 'run', 'task', candidate_id='stale', commit='a' * 40, decision='approve')
    decision = decide_approval(store, 'run', 'task', candidate_id='candidate-1', commit='a' * 40, decision='approve')
    assert decision['status'] == 'approved' and decision['manual_merge']
    assert decide_approval(store, 'run', 'task', candidate_id='candidate-1', commit='a' * 40, decision='approve') == decision
    assert register_approval(store, 'run', 'task', delivery) == decision
    assert not LifecycleStore(tmp_path).read_run('run')  # 不阻塞调度、不伪造执行状态。


def test_rejection_requires_reason_and_new_candidate_invalidates_old_decision(tmp_path):
    store = RecordStore(tmp_path)
    register_approval(store, 'run', 'task', {'candidate_id': 'c1', 'commit': 'a' * 40})
    with pytest.raises(ValueError):
        decide_approval(store, 'run', 'task', candidate_id='c1', commit='a' * 40, decision='reject')
    rejected = decide_approval(store, 'run', 'task', candidate_id='c1', commit='a' * 40, decision='reject', reason='wrong semantics')
    assert rejected['retry_status'] == 'pending'
    fresh = register_approval(store, 'run', 'task', {'candidate_id': 'c2', 'commit': 'b' * 40})
    assert fresh['status'] == 'pending'


def test_generated_carrier_final_does_not_pollute_source_diff(tmp_path):
    from infrastructure.workspace.diff import source_changes, complete_changes
    before, after = tmp_path / 'before', tmp_path / 'after'
    for root in (before, after):
        (root / 'gathers/Brazil').mkdir(parents=True)
        (root / 'gathers/Brazil/BrazilLogin.py').write_text('value = 1\n')
    (after / 'gathers/Brazil/final.json').write_text('{}')
    (after / 'gathers/Brazil/BrazilLogin.py').write_text('value = 2\n')
    assert source_changes(after, before) == ['gathers/Brazil/BrazilLogin.py']
    assert len(complete_changes(after, before)['files']) == 1


def test_acceptance_is_frozen_before_patch_and_cannot_be_rewritten(tmp_path):
    work, baseline = tmp_path / 'work', tmp_path / 'source_snapshot'
    work.mkdir(); baseline.mkdir()
    (baseline / 'main.py').write_text('value = 1')
    first = freeze_acceptance(work, {'error': 'original'}, str(baseline), {})
    (work / 'main.py').write_text('value = 2')
    assert freeze_acceptance(work, {'error': 'original'}, str(baseline), {}) == first
    with pytest.raises(ValueError):
        freeze_acceptance(work, {'error': 'changed'}, str(baseline), {})


def test_helper_detection_includes_nested_same_name_functions(tmp_path):
    from core.conventions import convention_errors
    before, after = tmp_path / 'before', tmp_path / 'after'
    before.mkdir(); after.mkdir()
    (before / 'main.py').write_text('def existing():\n    return 1\n')
    (after / 'main.py').write_text('def existing():\n    def existing():\n        return 2\n    return existing()\n')
    assert convention_errors(str(before), str(after), ['main.py'])


def test_real_git_publication_recovers_lost_receipt_and_rejects_changed_remote(tmp_path):
    from git import Repo
    from core.errors import DeliveryError
    from infrastructure.git.delivery import GitBranchDelivery, GitDeliveryConfig

    remote = Repo.init(tmp_path / 'remote.git', bare=True)
    repo = Repo.init(tmp_path / 'source')
    try:
        with repo.config_writer() as config:
            config.set_value('user', 'name', 'fixture')
            config.set_value('user', 'email', 'fixture@example.test')
        file = tmp_path / 'source/main.py'
        file.write_text('value = 1\n')
        repo.index.add(['main.py'])
        first = repo.index.commit('baseline').hexsha
        repo.create_remote('origin', str(remote.git_dir))
        file.write_text('value = 2\n')
        repo.index.add(['main.py'])
        fixed = repo.index.commit('fix').hexsha
        delivery = GitBranchDelivery(GitDeliveryConfig(push=True))
        receipt = {'repair_branch': 'repair/TEST', 'commit': fixed}
        state = {'run_id': 'run', 'task_id': 'task', 'allow_push': True}
        # 模拟远端成功、SQLite 尚未写入 receipt：重新消费同一候选不重复提交。
        repo.git.push('origin', f'{fixed}:refs/heads/repair/TEST')
        assert delivery._resume_delivery(repo, state, receipt)['pushed'] is True
        assert remote.commit('refs/heads/repair/TEST').hexsha == fixed
        # 对照仅改变远端引用；当前候选不得覆盖其他提交。
        remote.git.update_ref('refs/heads/repair/TEST', first)
        with pytest.raises(DeliveryError):
            delivery._resume_delivery(repo, state, receipt)
        assert remote.commit('refs/heads/repair/TEST').hexsha == first
        state['supersedes_commit'] = first
        assert delivery._resume_delivery(repo, state, receipt)['pushed']
        assert remote.commit('refs/heads/repair/TEST').hexsha == fixed
    finally:
        repo.close()
        remote.close()


def test_real_api_detail_approval_and_manual_close_contract(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from core.contracts import StepRecord
    from server.context import _authorize
    from server.routes import router

    monkeypatch.setenv('AI_REPAIR_RUNTIME_DIR', str(tmp_path))
    monkeypatch.delenv('SSLKEYLOGFILE', raising=False)
    store = RecordStore(tmp_path)
    store.create_run('api-run', {'workflow_version': WORKFLOW_VERSION})
    store.save('api-run', StepRecord(name='identify_carrier', title='identify', status='succeeded', output={
        'tasks': [{'task_id': 'TEST', 'carrier': 'TEST', 'biztype': 'track', 'problem': {}}]}))
    store.save('api-run', StepRecord(name='submit_changes', title='submit', status='succeeded', output={
        'candidate_id': 'c1', 'commit': 'a' * 40, 'pushed': True}), 'TEST')
    register_approval(store, 'api-run', 'TEST', {'candidate_id': 'c1', 'commit': 'a' * 40})
    app = FastAPI()
    app.dependency_overrides[_authorize] = lambda: None
    app.include_router(router)
    with TestClient(app) as client:
        detail = client.get('/api/v1/runs/api-run/tasks/TEST')
        assert detail.status_code == 200, detail.text
        assert detail.json()['actions']['close']['enabled']
        assert not detail.json()['actions']['retry']['enabled']  # 已发布交付不能因原始 MANUAL 标签重复派单。
        decision = client.post('/api/v1/approvals/api-run:TEST/decision', json={
            'candidate_id': 'c1', 'commit': 'a' * 40, 'decision': 'approve'})
        assert decision.status_code == 200 and decision.json()['manual_merge'] is True
        assert client.post('/api/v1/runs/api-run/tasks/TEST/close', json={'reason': ' '}).status_code == 422
        assert client.post('/api/v1/runs/api-run/tasks/TEST/close', json={'reason': '人工已处理'}).status_code == 200
        closed = client.get('/api/v1/runs/api-run/tasks/TEST').json()
        assert not closed['actions']['retry']['enabled']
        assert not closed['actions']['close']['enabled']
        assert client.post('/api/v1/runs/api-run/tasks/TEST/retry').status_code == 409


def test_retry_intent_cannot_be_overwritten_while_queued(tmp_path):
    store, operation = prepared_retry(tmp_path)
    operation.save('queued')
    with pytest.raises(RuntimeError):
        RetryOperation(store, 'run', 'task')


def test_new_approval_preserves_decided_candidate_history(tmp_path):
    store = RecordStore(tmp_path)
    register_approval(store, 'run', 'task', {'candidate_id': 'c1', 'commit': 'a' * 40})
    decide_approval(store, 'run', 'task', candidate_id='c1', commit='a' * 40,
                    decision='reject', reason='改变首项语义', reason_code='business')
    current = register_approval(store, 'run', 'task', {'candidate_id': 'c2', 'commit': 'b' * 40})
    assert current['history'][0]['reason_code'] == 'business'
    assert current['history'][0]['status'] == 'rejected'


@pytest.mark.parametrize('carrier, message, expected_rule', [
    ('Almex', "ValueError: time data '01/11/2026' does not match format '%Y-%m-%d'", 'timezone'),
    ('Spreetail', 'ValueError: unconverted data remains: 7; 2026-11-01T12:00:00.1234567', 'timezone'),
    ('DPDAT', 'mydpd.at WAF: Verifying your browser', 'block'),
])
def test_historical_carriers_keep_original_error_and_load_business_rules(carrier, message, expected_rule):
    from infrastructure.es.logs import ESLogAdapter
    from infrastructure.llm.rules import select_rules
    payload = {'carrier': carrier, 'requests': [{'number': 'fixture', 'gather_channel': 'Default'}]}
    line = 'ERROR gather.py.track - framework.track_server:解析异常,model:' + json.dumps(payload) + ', msg:' + message
    parsed = ESLogAdapter.parse_message(line)
    assert parsed and parsed['carrier'] == carrier
    assert message in parsed['error']
    assert expected_rule in select_rules(role='review', problem=parsed)


def test_execution_scope_serializes_independent_worker_processes(tmp_path):
    import subprocess
    import sys
    script = '''
import sys
from infrastructure.storage.lifecycle import LifecycleStore
store=LifecycleStore(sys.argv[1])
run=sys.argv[2]
claimed=store.claim_worker_lease(run, owner_id=run, lease_token=run)
print(claimed, flush=True)
if claimed and len(sys.argv)>3:
 sys.stdin.readline()
 store.release_worker_lease(run, owner_id=run, lease_token=run)
'''
    first = subprocess.Popen([sys.executable, '-c', script, str(tmp_path), 'one', 'hold'],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert first.stdout.readline().strip() == 'True'
        second = subprocess.run([sys.executable, '-c', script, str(tmp_path), 'two'],
                                capture_output=True, text=True, timeout=20, check=True)
        assert second.stdout.strip() == 'False'
        first.communicate('\n', timeout=20)
        third = subprocess.run([sys.executable, '-c', script, str(tmp_path), 'three'],
                               capture_output=True, text=True, timeout=20, check=True)
        assert third.stdout.strip() == 'True'
    finally:
        if first.poll() is None:
            first.terminate()
            first.communicate(timeout=20)


def test_artifact_preview_and_download_reject_database_and_path_escape(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from server.routes.common import _public_artifact_target
    (tmp_path / 'reports').mkdir()
    report = tmp_path / 'reports/final.json'
    report.write_text('{}')
    assert _public_artifact_target(tmp_path, 'reports/final.json') == report
    for path in ('state.sqlite3', '../outside.json', 'workspace/.git/config', 'reports/private.sqlite3', 'reports/final.json.abc.tmp'):
        with pytest.raises(HTTPException):
            _public_artifact_target(tmp_path, path)


@pytest.mark.parametrize('name', ['unified_fixer_agent', 'review_agent', 'generate_report', 'submit_changes'])
def test_single_step_enqueue_failure_restores_old_record_and_reports(tmp_path, name):
    from core.contracts import StepRecord, StepError
    from workflow.engine.runner import StepRunner, StepRunRequest
    store = RecordStore(tmp_path)
    store.create_run('run', {'workflow_version': WORKFLOW_VERSION})
    old = StepRecord(name=name, title=name, status='failed', summary='previous failure',
                     error=StepError(code='STEP_EXECUTION_FAILED', message='previous failure'))
    store.save('run', old, 'task')
    store.write_artifact('run', 'reports/final.json', {'attempt': 1}, 'task')
    runner = StepRunner(store)
    spec = SimpleNamespace(name=name, title=name, task_level=True)
    with patch.object(runner, 'validate', return_value=spec), patch('workflow.engine.runner._archive_step_retry'), \
         patch('threading.Thread.start', side_effect=RuntimeError('enqueue unavailable')):
        with pytest.raises(RuntimeError, match='enqueue unavailable'):
            runner.run(StepRunRequest(run_id='run', task_id='task', step_name=name))
    assert store.load('run', name, 'task').summary == 'previous failure'
    assert store.read_artifact('run', 'reports/final.json', 'task') == {'attempt': 1}
    assert store.database('run').get('', 'retry_intent')['phase'] == 'rolled_back'
    assert not LifecycleStore(tmp_path).read_run('run')['worker_active']


def test_report_archive_failure_compensates_completed_moves(tmp_path, monkeypatch):
    from pathlib import Path
    store = RecordStore(tmp_path)
    store.write_artifact('run', 'reports/final.json', {'run': 'old'})
    store.write_artifact('run', 'reports/final.json', {'task': 'old'}, 'task')
    failing = store.task_dir('run', 'task') / 'reports/final.json'
    replace = Path.replace

    def fail_task(source, target):
        if source == failing:
            raise OSError('archive unavailable')
        return replace(source, target)

    with monkeypatch.context() as change:
        change.setattr(Path, 'replace', fail_task)
        with pytest.raises(OSError, match='archive unavailable'):
            archive_retry_reports(store, 'run', 'task')
    assert store.read_artifact('run', 'reports/final.json') == {'run': 'old'}
    assert store.read_artifact('run', 'reports/final.json', 'task') == {'task': 'old'}
    archive_retry_reports(store, 'run', 'task')
    assert not failing.exists()


def test_large_log_body_is_archived_without_inflating_step_record(tmp_path):
    from core.contracts import StepRecord
    import importlib
    node = importlib.import_module('workflow.stages.log_pipeline.normalize_error.node')
    from workflow.stages.log_pipeline.normalize_error.models import NormalizeErrorInput
    from workflow.stages.log_pipeline.discover_logs.models import DiscoverInput
    store = RecordStore(tmp_path)
    store.create_run('run', {'workflow_version': WORKFLOW_VERSION})
    message = 'trace frame\n' * 35000
    row = node._normalize({'service_name': 'gather.py.track', 'biztype': 'framework.track_server',
        'carrier': 'TEST', 'req': '{"carrier":"TEST"}', 'error': 'ValueError: original', 'message': message}, 0)
    ctx = SimpleNamespace(store=store, run=SimpleNamespace(run_id='run'), services=SimpleNamespace(ignore_errors=[]),
                          emit_artifact=lambda name, path, body: store.write_artifact('run', path, body))
    query = DiscoverInput(service_name='gather.py.track', service_prefix='gather.py', hours=24, limit=1)
    with patch.object(node, '_query', return_value=query), patch.object(node, 'iter_normalized', return_value=iter([row])):
        output = node.run(ctx, NormalizeErrorInput(run_id='run'))
    store.save('run', StepRecord(name='normalize_error', title='normalize', status='succeeded', output=output.model_dump()))
    assert store.steps_path('run').stat().st_size < 5000
    assert store.read_artifact('run', 'artifacts/normalized_records.json')['records'][0]['problem']['message'] == message


def test_get_projection_does_not_settle_persisted_state_or_ignore_live_lease(tmp_path, monkeypatch):
    from core.contracts import StepRecord
    from server.routes.common import _run_payload
    monkeypatch.setenv('AI_REPAIR_RUNTIME_DIR', str(tmp_path))
    store = RecordStore(tmp_path)
    store.create_run('projection', {'workflow_version': WORKFLOW_VERSION})
    store.save('projection', StepRecord(name='identify_carrier', title='identify', status='succeeded', output={
        'tasks': [{'task_id': 'TEST', 'carrier': 'TEST', 'biztype': 'track', 'problem': {}}]}))
    store.save('projection', StepRecord(name='unified_fixer_agent', title='fix', status='running'), 'TEST')
    life = LifecycleStore(tmp_path)
    assert life.claim_worker_lease('projection', owner_id='one', lease_token='one', ttl_seconds=300)
    life.update_run('projection', status='running', heartbeat_at='2000-01-01T00:00:00+00:00')
    assert _run_payload(store, 'projection')['worker_active']
    life.release_worker_lease('projection', owner_id='one', lease_token='one')
    before = life.read_run('projection')
    task_before = life.read_task('projection', 'TEST')
    steps_before = store.steps_path('projection').read_bytes()
    projection = _run_payload(store, 'projection')
    assert projection['status'] == 'failed'
    assert life.read_run('projection') == before
    assert life.read_task('projection', 'TEST') == task_before
    assert store.steps_path('projection').read_bytes() == steps_before


def test_historical_duration_migrates_without_weakening_strict_step_contract():
    from core.contracts import StepRecord
    from pydantic import ValidationError
    legacy = {'name': 'review_agent', 'title': 'review', 'status': 'succeeded', 'elapsed_ms': 125}
    record = StepRecord.model_validate(legacy)
    assert record.duration_ms == 125
    assert 'elapsed_ms' not in record.model_dump()
    with pytest.raises(ValidationError):
        StepRecord.model_validate({**legacy, 'duration_ms': 126})
    with pytest.raises(ValidationError):
        StepRecord.model_validate({**legacy, 'passed': True})


def test_legacy_framework_ends_before_model_by_default(tmp_path):
    from agents.unified_fixer.graph import run_unified_fixer
    with patch('agents.unified_fixer.graph.create_provider') as provider:
        result = run_unified_fixer({'workspace_path': str(tmp_path), 'carrier': 'TEST',
                                   'evidence': {'framework': 'spiders'}}, None)
    provider.assert_not_called()
    assert result['changed_files'] == []
    assert result['skip_reason'] == '策略关闭旧框架分析'


def test_single_step_continuation_keeps_same_lease_and_global_slot(tmp_path):
    from core.contracts import StepRecord
    from workflow.engine.runner import StepRunner, StepRunRequest
    from agents.unified_fixer import SPEC
    store = RecordStore(tmp_path)
    life = LifecycleStore(tmp_path)
    assert life.claim_worker_lease('run', owner_id='worker', lease_token='token')
    store.set_worker_lease('run', 'worker', 'token')
    called = []

    def continuation(request, payload):
        assert life.owns_worker_lease('run', owner_id='worker', lease_token='token')
        assert not life.claim_worker_lease('other', owner_id='other', lease_token='other')
        store.write_artifact('run', 'artifacts/continuation.json', {'same_lease': True}, 'task')
        called.append(request.step_name)

    runner = StepRunner(store, on_continue=continuation)
    record = StepRecord(name=SPEC.name, title='fix', status='succeeded')
    with patch('workflow.engine.runner.execute_once', return_value=(0, record, {})):
        runner._execute(SPEC, StepRunRequest(run_id='run', task_id='task', step_name=SPEC.name, continue_after=True), 'worker', 'token')
    assert called == [SPEC.name]
    assert not life.read_run('run')['worker_active']
