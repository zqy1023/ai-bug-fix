"""真实浏览器验证完整清单、待执行详情、断线重连和无刷新状态推进。"""
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright, expect

ORIGINAL_LOG = '  原始解析异常,model:{"carrier":"ESPoirer","response":"system error"}\n' + '原始堆栈 frame\n' * 1000 + '\n END_OF_ORIGINAL  '


def main():
    base = os.environ.get('WORKBENCH_URL', 'http://127.0.0.1:8000')
    state = {'advanced': False, 'cancelled': False, 'events': 0}
    stages = [
        {'key': 'prepare_context', 'title': '准备上下文', 'members': ['prepare_workspace'], 'group': True},
        {'key': 'unified_fixer_agent', 'title': '分析与修复', 'members': ['unified_fixer_agent'], 'group': False},
        {'key': 'review_agent', 'title': '代码审查', 'members': ['review_agent'], 'group': False},
        {'key': 'generate_report', 'title': '生成报告', 'members': ['generate_report'], 'group': False},
    ]

    def tasks():
        return [dict(task_id=f'task-{index}', carrier=f'CARRIER-{index:02}', biztype='track',
                     result='CHANGED_DELIVERED' if index == 0 or (index == 1 and state['advanced']) else 'RUNNING' if index == 1 else 'PENDING',
                     steps_count=1 if index < 2 else 0,
                     step_statuses={'prepare_workspace': 'succeeded'} if index < 2 else {},
                     current_step='unified_fixer_agent' if index == 1 and not state['advanced'] else '',
                     lifecycle={}, terminal_reason='', report={}) for index in range(35)]

    def detail():
        return dict(run_id='live-demo', status='cancelled' if state['cancelled'] else 'running', terminal=state['cancelled'],
                    started_at='2026-10-29T10:00:00+08:00', task_count=35,
                    succeeded_tasks=2 if state['advanced'] else 1, changed_delivered_tasks=2 if state['advanced'] else 1, unchanged_delivered_tasks=0, failed_tasks=0,
                    waiting_tasks=0, pending_tasks=33, running_tasks=0 if state['advanced'] else 1,
                    completed_tasks=2 if state['advanced'] else 1, tasks=tasks(), stages=stages,
                    steps=[{'name': 'sync_source_repo', 'title': '同步源码', 'status': 'succeeded'}],
                    run_lifecycle={'progress': {'message': '正在读取并规范化 ES 日志', 'step': 'normalize_error',
                        'counts': {'es_hits': 212001 if state['advanced'] else 190001,
                                   'parsed': 211720, 'normalized_count': 211700, 'duplicate_count': 210986,
                                   'unique_count': 663, 'unparseable': 281, 'ignored_count': 51,
                                   'record_limit': 0}}, 'progress_at': '2026-10-30T03:18:29+00:00'},
                    config={}, actions={}, checkpoint={'resumable': False, 'next_nodes': []})

    def route_api(route):
        parsed = urlparse(route.request.url)
        path, query = parsed.path, parse_qs(parsed.query)
        if path.endswith('/events'):
            state['events'] += 1
            # 活动流正常 EOF 也必须重连；内容由同一份批次投影生成。
            route.fulfill(content_type='text/event-stream', body='event: run.snapshot\ndata: ' + json.dumps(detail()) + '\n\n')
            return
        if path.endswith('/health'):
            data = {'status': 'ok'}
        elif path.endswith('/dashboard/summary'):
            data = {'active_runs': 1, 'recent_runs': [], 'success_rate_7d': 0}
        elif path.endswith('/board'):
            rows = tasks()
            category = query.get('category', ['all'])[0]
            if category == 'pending': rows = [task for task in rows if task['result'] == 'PENDING']
            if category == 'changed_delivered': rows = [task for task in rows if task['result'] == 'CHANGED_DELIVERED']
            number = int(query.get('page', ['1'])[0])
            data = {'run_id': 'live-demo', 'summary': detail(), 'stages': stages, 'all_total': 35,
                    'total': len(rows), 'page': number, 'page_size': 20,
                    'counts': {'pending': 33, 'active': 0 if state['advanced'] else 1, 'changed_delivered': 2 if state['advanced'] else 1},
                    'tasks': rows[(number - 1) * 20:number * 20]}
        elif path.endswith('/problem-log'):
            data = {'message': ORIGINAL_LOG, 'trigger_message': 'common.base_func:Carrier:ESPoirer,msg:system error',
                    'related_messages': ['common.base_func:Carrier:ESPoirer,msg:system error'], 'truncated': False,
                    'from_archive': True, 'source_biztype': 'framework.track_server', 'message_format': 'model'}
        elif '/tasks/' in path:
            task_id = path.split('/tasks/')[1].split('/')[0]
            task = next(task for task in tasks() if task['task_id'] == task_id)
            data = {'run_id': 'live-demo', 'run_status': detail()['status'], 'run_terminal': state['cancelled'],
                    'stages': stages, 'task': {**task, 'steps': [], 'report': {}, 'problem': {}}, 'artifacts': [], 'config': {}, 'actions': {}}
        else:
            data = detail()
        route.fulfill(json=data)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1080}, device_scale_factor=1)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/api/v1/**', route_api)
        page.goto(base + '/runs/live-demo')
        page.wait_for_load_state('networkidle')
        expect(page.locator('tbody tr')).to_have_count(20)
        expect(page.get_by_text('1 / 35 个任务已结束', exact=False)).to_be_visible()
        expect(page.get_by_role('button', name='未开始 33', exact=True)).to_be_visible()
        page.get_by_role('button', name='下一页', exact=True).click()
        expect(page.locator('tbody tr')).to_have_count(15)
        expect(page.get_by_text('CARRIER-34', exact=True)).to_be_visible()
        page.get_by_role('button', name='未开始 33', exact=True).click()
        expect(page.locator('tbody tr')).to_have_count(20)
        scan = page.get_by_role('region', name='日志拉取进度')
        expect(scan.get_by_text('190,001', exact=True)).to_be_visible()
        expect(scan.get_by_text('全量扫描 · 总条数未知', exact=True)).to_be_visible()
        state['advanced'] = True
        expect(page.get_by_text('2 / 35 个任务已结束', exact=False)).to_be_visible(timeout=15000)
        expect(scan.get_by_text('212,001', exact=True)).to_be_visible(timeout=15000)
        for _ in range(100):
            if state['events'] >= 2:
                break
            page.wait_for_timeout(100)
        assert state['events'] >= 2, 'non-terminal EOF did not reconnect'
        output = Path(os.environ.get('LIVE_PROGRESS_SCREENSHOTS', str(Path(__file__).resolve().parents[3] / 'runtime/ui-check')))
        output.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(output / 'run-live-progress.png'), full_page=True)
        page.goto(base + '/runs/live-demo/tasks/task-34')
        page.wait_for_load_state('networkidle')
        expect(page.get_by_text('任务未开始', exact=True)).to_be_visible()
        expect(page.get_by_label('任务执行进度')).to_be_visible()
        expect(page.locator('.task-stage.pending')).to_have_count(4)
        original = page.get_by_label('完整异常 message').first
        expect(original).to_be_visible()
        assert original.text_content() == ORIGINAL_LOG
        expand = page.get_by_role('button', name='展开完整原文', exact=False)
        expect(expand).to_have_attribute('aria-expanded', 'false')
        expand.click()
        expect(page.get_by_role('button', name='收起原文')).to_have_attribute('aria-expanded', 'true')
        assert original.text_content() == ORIGINAL_LOG
        page.get_by_role('button', name='收起原文').click()
        page.screenshot(path=str(output / 'task-not-started.png'), full_page=True)
        state['cancelled'] = True
        page.goto(base + '/runs/live-demo')
        page.wait_for_load_state('networkidle')
        expect(page.get_by_role('button', name='未开始 33', exact=True)).to_be_visible()
        expect(page.locator('tbody tr')).to_have_count(20)
        page.set_viewport_size({'width': 390, 'height': 844})
        page.screenshot(path=str(output / 'run-live-mobile.png'), full_page=True)
        assert not errors, errors
        browser.close()
        print('PASS: complete task pages, pending filter/detail, SSE reconnect, live progress, cancelled pending rows; screenshots:', output)


if __name__ == '__main__':
    main()
