"""真实 API 与页面验证新任务分类、全清单和未开始详情。"""
import json
from pathlib import Path
import re
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from config.nacos import nacos_get
from infrastructure.llm.config import load_config
from playwright.sync_api import sync_playwright, expect, TimeoutError as PlaywrightTimeout


def main() -> None:
    config = load_config()
    run_id = json.loads((config.runtime_dir / 'audits/current.json').read_text())['run_id']
    origin = f'http://127.0.0.1:{config.workbench_api_port}'
    token = nacos_get('WORKBENCH_API_TOKEN', '').strip()
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    with urlopen(Request(f'{origin}/api/v1/runs/{run_id}/board?page_size=100', headers=headers), timeout=30) as response:
        board = json.load(response)
    output = config.runtime_dir / 'audits' / run_id
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1080}, extra_http_headers=headers)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'{origin}/runs/{run_id}')
        try:
            page.wait_for_load_state('networkidle', timeout=1500)
        except PlaywrightTimeout:
            pass  # 活跃批次的 SSE 持续连接，改用真实渲染与 API 数据作为就绪条件。
        expect(page.get_by_role('button', name=re.compile('^已改动交付'))).to_be_visible(timeout=30000)
        for label in ('无改动交付', '已跳过', '失败', '未开始', '执行中'):
            expect(page.get_by_role('button', name=re.compile('^' + label))).to_be_visible()
        assert '需人工' not in page.locator('body').inner_text()
        expect(page.locator('tbody tr')).to_have_count(min(board['all_total'], 20))
        page.screenshot(path=str(output / 'task-outcomes-desktop.png'), full_page=True)
        page.get_by_role('button', name=re.compile('^未开始')).click()
        pending = next((task for task in reversed(board['tasks']) if task['result'] == 'PENDING'), None)
        if pending:
            row = page.locator('tbody tr').filter(has_text=pending['task_id'])
            expect(row).to_be_visible(timeout=30000)
            row.click()
            expect(page.get_by_text('任务未开始', exact=True)).to_be_visible(timeout=30000)
            expect(page.get_by_text('原始异常读取失败', exact=False)).to_have_count(0)
        page.goto(f'{origin}/runs/{run_id}')
        expect(page.get_by_role('button', name=re.compile('^已改动交付'))).to_be_visible(timeout=30000)
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), '页面横向溢出'
        page.screenshot(path=str(output / 'task-outcomes-mobile.png'), full_page=True)
        assert not errors, errors
        browser.close()
    print('PASS: real API, four terminal categories, complete catalog, pending details, mobile layout')


if __name__ == '__main__':
    main()
