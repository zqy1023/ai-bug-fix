"""保存真实批次状态与交接审计；不启动任务、不修改运行记录。"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.nacos import nacos_get
from infrastructure.llm.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_id', nargs='?')
    args = parser.parse_args()
    config = load_config()
    run_id = args.run_id or json.loads((config.runtime_dir / 'audits/current.json').read_text())['run_id']
    destination = config.runtime_dir / 'audits' / run_id
    destination.mkdir(parents=True, exist_ok=True)
    token = nacos_get('WORKBENCH_API_TOKEN', '').strip()
    headers = {'Authorization': 'Bearer ' + token} if token else {}

    def get(path: str) -> dict:
        with urlopen(Request(f'http://127.0.0.1:{config.workbench_api_port}/api/v1{path}', headers=headers), timeout=45) as response:
            return json.load(response)

    run = get(f'/runs/{run_id}')
    board = get(f'/runs/{run_id}/board?page_size=100')
    tasks = board['tasks']
    for page in range(2, (board['all_total'] + 99) // 100 + 1):
        tasks.extend(get(f'/runs/{run_id}/board?page_size=100&page={page}')['tasks'])
    issues = []
    allowed = {'PENDING', 'RUNNING', 'PAUSED', 'CHANGED_DELIVERED', 'UNCHANGED_DELIVERED', 'SKIPPED', 'FAILED'}
    if sum(board['counts'].values()) != board['all_total']:
        issues.append('互斥分类计数不等于全部任务数量')
    details = []
    for task in tasks:
        if task['result'] not in allowed:
            issues.append(f"{task['task_id']}: 非当前任务状态 {task['result']}")
        if task['result'] in {'PENDING', 'RUNNING', 'PAUSED'}:
            continue
        detail = get(f"/runs/{run_id}/tasks/{task['task_id']}")
        details.append(detail)
        if detail['task']['result'] != task['result']:
            issues.append(f"{task['task_id']}: 列表与详情终态不一致")
        if task['result'] == 'CHANGED_DELIVERED':
            report = detail['task']['report']
            if not report.get('changed_files') or not detail['delivery'].get('commit'):
                issues.append(f"{task['task_id']}: 已改动交付缺少文件或提交证据")
            review = report.get('review') or {}
            if review.get('code_verdict') != 'PASS':
                issues.append(f"{task['task_id']}: 交付源码未经独立代码批准")
            coverage = review.get('coverage') or {}
            if coverage.get('decision') != 'sufficient' or coverage.get('missing_checks'):
                issues.append(f"{task['task_id']}: 交付源码的关键审查覆盖仍有缺口")
            if not {'syntax', 'business', 'scope'} <= set(review.get('dimensions') or {}):
                issues.append(f"{task['task_id']}: 审查缺少职责维度")
    snapshot = {'recorded_at': datetime.now(timezone.utc).isoformat(), 'run': run,
                'board': board, 'details': details, 'protocol_issues': issues,
                'semantic_review': '尚需逐任务核对原文、源码差异与模型证据；协议检查不能证明业务正确'}
    (destination / 'snapshot.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    brief = {'status': run['status'], 'terminal': run.get('terminal'), 'worker_active': run.get('worker_active'),
             'phase': run.get('run_lifecycle', {}).get('current_step'), 'counts': board['counts'],
             'progress': run.get('run_lifecycle', {}).get('progress'),
             'tasks': [{'carrier': task['carrier'], 'task_id': task['task_id'], 'result': task['result'],
                        'step': task.get('current_step'), 'reason': task.get('terminal_reason')} for task in tasks],
             'protocol_issues': issues}
    with (destination / 'transitions.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'at': snapshot['recorded_at'], **brief}, ensure_ascii=False) + '\n')
    print(json.dumps(brief, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
