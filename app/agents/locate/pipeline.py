"""分析阶段统一拥有策略快照、验收目标和修复前复现。"""
from types import SimpleNamespace
from app.agents.locate.analysis_checks import AnalysisChecksInput, BaselineReplayInput, build_acceptance_contract, run_baseline_reproduction
from app.workflow.policy import policy_of, resolve_policy, skipped
from app.application.audit import StepRecord
from app.domain.models import utc_now


def build_analysis_pipeline(store, services, model_node):
    def node(state):
        state = dict(state)
        meta = store.load_run(state['run_id'])
        task = state.get('task') or {}
        path = 'policy/effective.json'
        if (store.task_dir(state['run_id'], state['task_id']) / path).is_file():
            snapshot = store.read_artifact(state['run_id'], path, state['task_id'])
        else:
            values = resolve_policy(meta.get('agent_policy'), (meta.get('task_policies') or {}).get(state['task_id'], task.get('agent_policy')))
            snapshot = {'values': values, 'sources': {key: 'task' if key in (task.get('agent_policy') or {}) else 'batch' if key in (meta.get('agent_policy') or {}) else 'system' for key in values}}
            store.write_artifact(state['run_id'], path, snapshot, state['task_id'])
        state.update(agent_policy=snapshot['values'], policy_sources=snapshot['sources'])
        policy = policy_of(state)
        def contract_for(diagnosis):
            return build_acceptance_contract(AnalysisChecksInput(generate_criteria=policy.acceptance, problem=state['problem'], baseline_dir=state['baseline_dir'], baseline_manifest=state['baseline_manifest'], diagnosis=diagnosis))
        contract = contract_for({})
        state['acceptance_contract'] = contract.model_dump(mode='json')
        checks = {'acceptance': {'status': 'completed'} if policy.acceptance else skipped()}
        if policy.reproduce:
            ctx = SimpleNamespace(services=services, emit_artifact=lambda name, target, value: store.write_artifact(state['run_id'], target, value, state['task_id']))
            baseline = run_baseline_reproduction(ctx, BaselineReplayInput(task_id=state['task_id'], carrier=task['carrier'], problem=state['problem'], baseline_dir=state['baseline_dir'], baseline_manifest=state['baseline_manifest'], source_project_path=state['source_project_path'], ai_workspace_path=state['ai_workspace_path'], acceptance_contract=contract, candidates=(state.get('evidence') or {}).get('candidates', []))).model_dump(mode='json')
        else:
            baseline = {**skipped(), 'problem_id': contract.problem_id, 'baseline_id': contract.baseline_id, 'retryable': False, 'report_path': ''}
        checks['reproduction'] = baseline
        if not policy.acceptance:
            checks['acceptance']['reason'] = '用户关闭任务验收条件输出；基础协议与源码身份校验仍执行'
        state.update(baseline_reproduction=baseline, analysis_checks=checks)
        legacy = bool((state.get('evidence') or {}).get('legacy_only') or state.get('framework') == 'spiders')
        if legacy and not policy.analyze_legacy:
            locate = {'status': 'MANUAL', 'fault_origin': 'unknown', 'category': 'unknown', 'summary': '旧框架分析已跳过（用户关闭）', 'recommended_scope': 'manual', 'passed': False}
            store.save(state['run_id'], StepRecord(name='locate_agent', title='分析模型', status='skipped', summary=locate['summary'], started_at=utc_now(), finished_at=utc_now(), output=locate), state['task_id'])
            patch = {'locate': locate, 'last_result': 'manual'}
        else:
            patch = model_node(state)
            locate = patch.get('locate') or {}
        if not policy.acceptance:
            locate['acceptance_criteria'] = []
            patch['locate'] = locate
        contract = contract_for(locate)
        allowed = locate.get('fault_origin') in {'local_code', 'mixed'} and locate.get('status') == 'LOCATED'
        shared = locate.get('category') == 'shared' or locate.get('recommended_scope') == 'project'
        if shared and (not policy.repair_shared or not policy.shared_paths):
            allowed = False
            checks['repair_permission'] = {'status': 'skipped', 'reason_code': 'permission_denied', 'reason': '共享层修复未授权或未指定目录'}
        if legacy and not policy.repair_legacy:
            allowed = False
            checks['repair_permission'] = {'status': 'skipped', 'reason_code': 'permission_denied', 'reason': '旧框架修复未授权'}
        if not allowed and 'repair_permission' not in checks:
            checks['repair_permission'] = {'status': 'skipped', 'reason': '本次归因不支持自动修改', 'reason_code': 'analysis_only'}
        if not allowed:
            patch.update(last_result='manual', terminal_status='MANUAL', retry_target='')
        else:
            patch.update(last_result='passed', terminal_status='passed', scope_escalation_required=False)
        patch.update(agent_policy=state['agent_policy'], policy_sources=state['policy_sources'], analysis_checks=checks, acceptance_contract=contract.model_dump(mode='json'), baseline_reproduction=baseline, analysis_only=not allowed, max_attempts=policy.max_repairs)
        record = store.load(state['run_id'], 'locate_agent', state['task_id'])
        if record:
            record.output = {**locate, 'checks': checks, 'acceptance_contract': patch['acceptance_contract'], 'baseline_reproduction': baseline, 'agent_policy': state['agent_policy']}
            store.save(state['run_id'], record, state['task_id'])
        store.write_artifact(state['run_id'], 'artifacts/analysis.json', {**locate, 'checks': checks, 'acceptance_contract': patch['acceptance_contract']}, state['task_id'])
        from app.workflow.handoff import apply_step_result, outcome_of
        outcome = outcome_of(record.output if record else locate, summary=locate.get('summary', ''), business=locate.get('status'))
        return {**patch, **apply_step_result(store, state, 'locate_agent', patch, outcome)}
    return node

