"""Archive and remove affected delivery branches, then replay original task inputs.

The parent run must be paused without an active worker. A fresh process executes
the current workflow and records results in a separate run; original evidence is
retained. Remote deletion uses both an atomic push and an expected-SHA lease.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import uuid
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import utc_now
from core.contracts import TaskSnapshot, StepRecord, WORKFLOW_VERSION
from core.keys import repair_branch
from config.settings import settings
from config.nacos import nacos_get
from git import Repo
from infrastructure.git.source import git_auth_env
from infrastructure.git.branches import delete_local_branch
from infrastructure.llm.config import load_config, workflow_recursion_limit
from infrastructure.llm.runtime import close_sessions
from infrastructure.storage.lifecycle import LifecycleStore
from infrastructure.storage.store import RecordStore
from workflow.engine.graph import (
    build_graph, build_services, prepare_run, _write_run_report_if_terminal,
    _write_run_report,
)
from workflow.engine.routes import IDENTIFY


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def require_project_interpreter() -> None:
    """在任何分支或运行状态操作前拒绝错误的平台解释器。"""
    environment = ROOT / '.venv'
    expected = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if expected.is_file() and Path(sys.prefix).resolve() != environment.resolve():
        raise RuntimeError(f'重跑必须使用项目解释器 {expected}；当前为 {sys.executable}')


def parent_status(run_id: str) -> dict:
    config = load_config()
    token = nacos_get('WORKBENCH_API_TOKEN', '').strip()
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    request = Request(f'http://127.0.0.1:{config.workbench_api_port}/api/v1/runs/{run_id}', headers=headers)
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def remote_heads(repo: Repo, remote: str) -> dict[str, str]:
    output = repo.git.ls_remote('--heads', remote, 'refs/heads/ai-bug-tasks/*')
    return {parts[1].removeprefix('refs/heads/'): parts[0]
            for line in output.splitlines() if len(parts := line.split()) == 2}


def plan(source_run: str, include: set[str]) -> dict:
    config = load_config()
    store = RecordStore(config.runtime_dir)
    selected = []
    for directory in sorted((store.run_dir(source_run) / 'tasks').iterdir()):
        state_path = directory / 'workflow/state.json'
        if not state_path.is_file():
            continue
        state = TaskSnapshot.model_validate(json.loads(state_path.read_text(encoding='utf-8'))['state']).to_graph()
        task = state['task']
        budget_events = []
        event_path = directory / 'audit/agent_events.jsonl'
        for line in event_path.read_text(encoding='utf-8').splitlines() if event_path.is_file() else []:
            row = json.loads(line)
            event = row.get('event', {})
            reason = event.get('reason', '')
            if (event.get('type') == 'tool_decision' and event.get('decision') == 'deny'
                    and any(word in reason for word in ('轮数', '预算', 'budget'))):
                budget_events.append({'call_id': row.get('model_call_id'), 'agent': row.get('agent_role'),
                                      'tool': event.get('tool'), 'reason': reason})
        reasons = []
        if budget_events:
            reasons.append('budget_blocked')
        if task['carrier'] in include:
            reasons.append('confirmed_prior_repair_or_review_issue')
        if not reasons:
            continue
        delivery = state.get('delivery') or {}
        branch = state.get('repair_branch') or repair_branch(task['biztype'], task['carrier'], settings.repair_branch_prefix, task['problem_key'])
        if not branch.startswith('ai-bug-tasks/'):
            raise ValueError('Unexpected branch namespace')
        selected.append({'task': task, 'reasons': reasons, 'budget_events': budget_events,
                         'branch': branch, 'expected_commit': delivery.get('commit'),
                         'previously_pushed': bool(delivery.get('pushed'))})
    priority = {'Coupang': 0, 'DPWORLD': 1, 'Abcourier': 2, 'Brazil': 3}
    selected.sort(key=lambda item: (priority.get(item['task']['carrier'], 4), item['task']['task_id']))
    if not selected:
        raise ValueError('No affected tasks')
    return {'source_run': source_run, 'new_run': uuid.uuid4().hex, 'created_at': utc_now(),
            'status': 'planned', 'tasks': selected}


def execute(manifest: dict, output: Path) -> None:
    require_project_interpreter()
    config = load_config()
    store = RecordStore(config.runtime_dir)
    parent = parent_status(manifest['source_run'])
    if parent['status'] != 'paused' or parent['worker_active'] or not parent['checkpoint']['resumable']:
        raise RuntimeError('Parent run has not reached a resumable safe pause')
    manifest['parent_checkpoint'] = parent['checkpoint']
    run_id = manifest['new_run']
    if store.run_dir(run_id).exists():
        raise RuntimeError('Replay run already exists; do not repeat branch deletion')
    # Compile before deleting anything, so import/contract failures preserve remote branches.
    graph = build_graph(store=store, services=build_services(config=config), config=config)
    repo = Repo(config.source_repo)
    if repo.is_dirty(untracked_files=False):
        raise RuntimeError('Source checkout has uncommitted changes')
    manifest['source_commit'] = repo.head.commit.hexsha
    env = git_auth_env(config.git_username, config.git_password)
    with repo.git.custom_environment(**env):
        heads = remote_heads(repo, config.git_remote)
        deleting = []
        for item in manifest['tasks']:
            actual = heads.get(item['branch'])
            if actual and actual != item['expected_commit']:
                raise RuntimeError('Remote branch differs from archived delivery: ' + item['branch'])
            item['remote_before'] = actual
            if actual:
                item['backup_ref'] = 'refs/ai-bug-fixer-replay-backup/' + run_id + '/' + hashlib.sha256(item['branch'].encode()).hexdigest()[:16]
                deleting.append(item)
        manifest['status'] = 'verified'
        write_json(output / 'manifest.json', manifest)
        if deleting:
            repo.git.fetch('--no-tags', config.git_remote,
                           *[f"refs/heads/{item['branch']}:{item['backup_ref']}" for item in deleting])
            for item in deleting:
                if repo.commit(item['backup_ref']).hexsha != item['remote_before']:
                    raise RuntimeError('Backup fetch observed a changed remote branch')
            bundle = output / 'old-delivery-branches.bundle'
            repo.git.bundle('create', str(bundle), *[item['backup_ref'] for item in deleting])
            repo.git.bundle('verify', str(bundle))
            manifest['bundle_sha256'] = hashlib.sha256(bundle.read_bytes()).hexdigest()
            manifest['status'] = 'backed_up'
            write_json(output / 'manifest.json', manifest)
            # All-or-nothing deletion, each branch protected against concurrent changes.
            repo.git.push('--atomic',
                          *[f"--force-with-lease=refs/heads/{item['branch']}:{item['remote_before']}" for item in deleting],
                          config.git_remote, *[f":refs/heads/{item['branch']}" for item in deleting])
        remaining = remote_heads(repo, config.git_remote)
        if any(item['branch'] in remaining for item in manifest['tasks']):
            raise RuntimeError('Remote deletion verification failed')
        for item in deleting:
            item['local_deleted'] = delete_local_branch(repo, item['branch'], expected_commit=item['remote_before'], source='replay')
            item['remote_deleted'] = True
    if repo.head.commit.hexsha != manifest['source_commit']:
        raise RuntimeError('Source HEAD changed during cleanup')
    manifest['status'] = 'branches_deleted'
    write_json(output / 'manifest.json', manifest)
    original = store.load_run(manifest['source_run'])
    _, _, store = prepare_run(run_id, overrides={'agent_policy': original.get('agent_policy') or {},
                                                'max_attempts': original.get('max_attempts', 3)})
    tasks = [item['task'] for item in manifest['tasks']]
    store.write_artifact(run_id, 'audit/replay-origin.json', manifest)
    seed = {key: original[key] for key in ('source_repo_path', 'log_service_name', 'log_service_prefix',
            'log_excluded_messages', 'log_excluded_biztypes', 'log_hours', 'log_limit', 'agent_policy',
            'max_attempts', 'allow_push', 'require_push') if key in original}
    seed.update(run_id=run_id, schema_version=2, workflow_version=WORKFLOW_VERSION,
                review_mode='agent', tasks=tasks, task_cursor=0, task_results=[], attempt=1, last_result='passed')
    identified = {'tasks': tasks, 'created': len(tasks), 'manual': 0}
    store.save(run_id, StepRecord(name=IDENTIFY, title='重放已归档的原始任务输入', status='succeeded',
               started_at=utc_now(), finished_at=utc_now(), attempt=1,
               summary=f'从 {manifest["source_run"]} 定向重跑 {len(tasks)} 个受影响任务；不重新拉取日志', output=identified))
    store.write_artifact(run_id, f'workflow/outputs/{IDENTIFY}.json',
                         {'workflow_version': WORKFLOW_VERSION, 'step': IDENTIFY, 'outcome': {'output': identified}})
    cfg = {'configurable': {'thread_id': run_id}, 'recursion_limit': workflow_recursion_limit(
        max_attempts=seed['max_attempts'], task_count_hint=len(tasks))}
    graph.update_state(cfg, seed, as_node=IDENTIFY)
    lifecycle = LifecycleStore(store.root)
    lifecycle.update_run(run_id, status='running', worker_active=True, phase='targeted_replay', allow_reactivate=True)
    stop = threading.Event()

    def pulse():
        while not stop.wait(5):
            lifecycle.update_run(run_id, heartbeat_at=utc_now(), worker_active=True)

    heartbeat = threading.Thread(target=pulse, daemon=True)
    heartbeat.start()
    manifest.update(status='running', started_at=utc_now(), worker_pid=os.getpid())
    write_json(output / 'manifest.json', manifest)
    print(json.dumps({'run_id': run_id, 'tasks': len(tasks), 'deleted': len(deleting)}, ensure_ascii=False), flush=True)
    try:
        final = graph.invoke(None, cfg)
        _write_run_report_if_terminal(store, run_id, graph, final_state=final)
        snapshot = graph.get_state(cfg)
        if snapshot.next:
            lifecycle.update_run(run_id, status='paused', phase='paused')
            manifest['status'] = 'paused'
        else:
            report = json.loads((store.run_dir(run_id) / 'reports/final.json').read_text(encoding='utf-8'))
            result = report['result']
            lifecycle.update_run(run_id, status='succeeded' if result == 'SUCCESS' else 'partial_failed' if result == 'PARTIAL_FAILED' else 'failed',
                                 terminal_reason=report['terminal_reason'], phase=report['terminal_step'])
            manifest.update(status='completed', result=result, task_results=report['task_results'])
    except Exception as error:
        _write_run_report(store, run_id, error=error)
        lifecycle.update_run(run_id, status='failed', error_type=type(error).__name__, terminal_reason=str(error))
        manifest.update(status='failed', error_type=type(error).__name__)
        raise
    finally:
        stop.set()
        heartbeat.join(timeout=1)
        close_sessions(run_id, None)
        lifecycle.update_run(run_id, worker_active=False)
        manifest['finished_at'] = utc_now()
        write_json(output / 'manifest.json', manifest)


def main():
    # 平台与 SDK 固定使用项目环境；采集验证另由 AI_REPAIR_RUNTIME_PYTHON 决定。
    require_project_interpreter()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_run')
    parser.add_argument('--include-carrier', action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    manifest_path = output / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest['source_run'] != args.source_run or manifest['status'] != 'planned':
            raise RuntimeError('Existing manifest is not an unexecuted plan for this run')
    else:
        manifest = plan(args.source_run, set(args.include_carrier))
        write_json(manifest_path, manifest)
    if args.execute:
        execute(manifest, output)
    else:
        print(json.dumps({'new_run': manifest['new_run'], 'tasks': len(manifest['tasks']),
                          'carriers': [item['task']['carrier'] for item in manifest['tasks']]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
