"""Verify actual task results, source differences, report-only delivery and mobile layout."""
import argparse
import copy
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from config.nacos import nacos_get
from playwright.sync_api import sync_playwright, expect, TimeoutError as PlaywrightTimeout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    origin = 'http://127.0.0.1:8000'
    headers = {'Authorization': 'Bearer ' + nacos_get('WORKBENCH_API_TOKEN', '').strip()}
    base = f'{origin}/runs/{args.run_id}'
    with urlopen(Request(f'{origin}/api/v1/runs/{args.run_id}/board?page_size=100', headers=headers), timeout=30) as response:
        board = json.load(response)
    changed = next(task for task in board['tasks'] if task['carrier'] == 'Coupang' and task['result'] == 'CHANGED_DELIVERED')
    unchanged = next(task for task in board['tasks'] if task['result'] == 'UNCHANGED_DELIVERED')
    pending = next((task for task in reversed(board['tasks']) if task['result'] == 'PENDING'), None)
    output = ROOT / 'runtime/ui-check/task-results'
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1080}, extra_http_headers=headers)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def visit(path):
            page.goto(path)
            try:
                page.wait_for_load_state('networkidle', timeout=1500)
            except PlaywrightTimeout:
                pass  # Active run SSE remains open; verify rendered content below.

        task_url = f'{base}/tasks/{changed["task_id"]}'
        visit(task_url)
        expect(page.locator('#problem-analysis')).to_contain_text('IndexError', timeout=30000)
        expect(page.locator('#repair-result')).to_contain_text('原有问题')
        expect(page.locator('#repair-result')).to_contain_text('具体修改')
        expect(page.locator('#repair-result')).to_contain_text('影响边界')
        expect(page.locator('.delivery-status.with-code.ok')).to_contain_text('已改动交付')
        expect(page.locator('#code-changes .change-line.add')).to_contain_text("or ['']", timeout=30000)
        expect(page.locator('#code-changes .change-line.del')).to_contain_text('location = event.xpath')
        assert page.locator('#problem-analysis').bounding_box()['y'] < page.get_by_text('异常原文', exact=True).bounding_box()['y']
        expect(page.locator('.check-why').first).not_to_have_text('—')
        page.locator('#problem-analysis summary').filter(has_text='分析依据').click()
        expect(page.locator('#problem-analysis .result-list').first).to_be_visible()
        page.locator('#code-changes').get_by_role('button', name='修改后', exact=True).click()
        expect(page.locator('#code-changes .change-code')).to_contain_text("or ['']")
        page.screenshot(path=str(output / 'changed-desktop.png'), full_page=True)

        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'changed task overflows mobile'
        page.screenshot(path=str(output / 'changed-mobile.png'), full_page=True)
        visit(task_url + '/change')
        expect(page.locator('#repair-result')).to_contain_text('具体修改')
        expect(page.locator('#code-changes .change-line.add')).to_contain_text("or ['']")
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'diff page overflows mobile'

        visit(f'{base}/tasks/{unchanged["task_id"]}')
        expect(page.locator('.delivery-status.report-only.info')).to_contain_text('无改动交付', timeout=30000)
        expect(page.locator('#repair-result')).to_contain_text('仅交付分析报告')
        expect(page.locator('#code-changes')).to_contain_text('未交付源码改动')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'report task overflows mobile'
        page.screenshot(path=str(output / 'unchanged-mobile.png'), full_page=True)

        if pending:
            visit(f'{base}/tasks/{pending["task_id"]}')
            expect(page.locator('#problem-analysis')).to_contain_text('未开始分析', timeout=30000)
            expect(page.locator('#repair-result')).to_contain_text('尚未开始修复')
            expect(page.locator('#code-changes')).to_contain_text('尚无代码差异')

        # A task still running has no final report: use its recorded Agent output.
        api_url = f'{origin}/api/v1/runs/{args.run_id}/tasks/{changed["task_id"]}'
        with urlopen(Request(api_url, headers=headers), timeout=30) as response:
            fixture = copy.deepcopy(json.load(response))
        fixture['task']['report'] = {}
        fixture['task']['result'] = 'RUNNING'
        fixture['task']['lifecycle']['status'] = 'running'
        page.route(api_url, lambda route: route.fulfill(json=fixture))
        visit(task_url)
        expect(page.locator('#problem-analysis')).to_contain_text('IndexError', timeout=30000)
        expect(page.locator('#repair-result')).to_contain_text('尚未交付')
        expect(page.locator('#repair-result')).to_contain_text('具体修改')
        page.unroute(api_url)

        page.set_viewport_size({'width': 1440, 'height': 1080})
        visit(base)
        expect(page.locator('.delivery-status.with-code.ok').first).to_be_visible(timeout=30000)
        expect(page.locator('.delivery-status.report-only.info').first).to_be_visible()
        assert not errors, errors
        browser.close()
    print('PASS: real changed/report-only/pending tasks, detailed findings and edits, source views, live-result fallback, distinct status badges, mobile layout')


if __name__ == '__main__':
    main()
