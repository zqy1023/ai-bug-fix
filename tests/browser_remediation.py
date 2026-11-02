"""连接隔离 fixture 服务，验证最终审批与实际代码差异页面。"""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

output = Path(os.environ['AI_REPAIR_RUNTIME_DIR']).parent / 'browser-receipt'
output.mkdir(parents=True, exist_ok=True)
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:5179/approvals')
    page.wait_for_load_state('networkidle')
    page.get_by_role('heading', name='最终审批', exact=True).wait_for()
    page.get_by_role('button', name='处理候选').first.wait_for()
    pending_count = page.get_by_role('button', name='处理候选').count()
    page.screenshot(path=str(output / 'approval-pending.png'), full_page=True)
    href = page.get_by_role('link', name='查看原始问题、审查及代码差异').first.get_attribute('href')
    page.get_by_role('button', name='处理候选').first.click()
    page.get_by_role('button', name='批准，等待人工合并').click()
    page.wait_for_function("count => [...document.querySelectorAll('button')].filter(b => b.textContent === '处理候选').length === count - 1", arg=pending_count)
    page.get_by_label('审批状态').select_option('approved')
    page.get_by_role('link', name='查看原始问题、审查及代码差异').first.wait_for()
    approved = page.request.get('http://127.0.0.1:8765/api/v1/approvals?status=approved').json()
    assert approved['items'][0]['manual_merge'] is True
    page.goto('http://127.0.0.1:5179' + href)
    page.wait_for_load_state('networkidle')
    page.get_by_text('实际代码差异', exact=True).wait_for()
    page.get_by_text('原始异常', exact=True).wait_for()
    page.get_by_text('fixture: missing optional location', exact=False).first.wait_for()
    page.get_by_text('gathers/TEST/main.py', exact=False).first.wait_for()
    assert page.get_by_role('button', name='重试任务', exact=True).is_disabled()
    page.screenshot(path=str(output / 'actual-diff.png'), full_page=True)
    assert 'kirby' not in page.content()
    assert not errors, errors
    (output / 'receipt.json').write_text(json.dumps({'approved': True, 'manual_merge': True,
        'actual_diff_visible': True, 'browser_errors': errors, 'scope': '隔离真实API与前端；无外部模型/Git/承运商调用'}, ensure_ascii=False, indent=2), encoding='utf-8')
    browser.close()
