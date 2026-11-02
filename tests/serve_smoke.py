"""只为浏览器回归提供隔离数据；不运行工作流、模型、承运商或远端Git。"""
import os
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['NACOS_ENABLED'] = 'false'

from fastapi import FastAPI
from core.contracts import StepRecord, WORKFLOW_VERSION
from infrastructure.storage.store import RecordStore
from infrastructure.storage.lifecycle import LifecycleStore
from infrastructure.storage.final_approval import register_approval
from server.context import _authorize
from server.routes import router


def main():
    import uvicorn
    root = Path(os.environ['AI_REPAIR_RUNTIME_DIR']).resolve()
    store = RecordStore(root)
    run_id, task_id = 'ui-' + uuid4().hex[:12], 'TEST__track__fixture'
    store.create_run(run_id, {'workflow_version': WORKFLOW_VERSION, 'log_hours': 24, 'log_limit': 1,
                               'review_mode': 'agent', 'max_attempts': 3})
    problem = {'carrier': 'TEST', 'biztype': 'track', 'service_name': 'fixture', 'message': 'fixture: missing optional location'}
    store.save(run_id, StepRecord(name='identify_carrier', title='识别任务', status='succeeded', output={
        'tasks': [{'task_id': task_id, 'carrier': 'TEST', 'biztype': 'track', 'problem': problem}]}))
    delivery = {'candidate_id': 'fixture-candidate', 'commit': 'a' * 40,
                'repair_branch': 'fixture/TEST', 'pushed': True, 'local_branch': False}
    store.save(run_id, StepRecord(name='submit_changes', title='提交分支', status='succeeded', output=delivery), task_id)
    store.write_artifact(run_id, 'reports/final.json', {'result': 'CHANGED_DELIVERED', 'status': 'CHANGED_DELIVERED',
        'carrier': 'TEST', 'biztype': 'track', 'problem': problem, 'root_cause': '可选地点缺失时取首项越界',
        'changed_files': ['gathers/TEST/main.py'], 'delivery': delivery, 'facts': ['仅对缺失地点容错'],
        'acceptance_criteria': ['保留已有首项选择'], 'review': {}, 'completed_steps': []}, task_id)
    store.write_artifact(run_id, 'artifacts/diff.json', {'available': True, 'files': [{
        'path': 'gathers/TEST/main.py', 'available': True, 'before_exists': True, 'after_exists': True,
        'before': 'location = values[0]\n', 'after': 'location = values[0] if values else ""\n'}]}, task_id)
    life = LifecycleStore(root)
    life.update_task(run_id, task_id, status='succeeded')
    life.update_run(run_id, status='succeeded', worker_active=False)
    register_approval(store, run_id, task_id, delivery)
    app = FastAPI()
    app.dependency_overrides[_authorize] = lambda: None
    app.include_router(router)
    uvicorn.run(app, host='127.0.0.1', port=8765, log_level='warning')


if __name__ == '__main__':
    main()
