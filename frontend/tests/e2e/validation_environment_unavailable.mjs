import { chromium } from 'playwright';
import assert from 'node:assert/strict';

/**
 * 「环境未就绪」必须如实显示，不能被显示成"未验证"或"已验证"。
 *
 * 后端在采集框架加载不起来时会给出
 *   validation.replay = {status: 'unverified', outcome: 'environment_unavailable', ...}
 * 这时验证根本没跑到被测代码。若前端把它压成"未验证"，读界面的人会以为
 * "业务没通过"，而实际上一次都没跑过——这是会误导复核的展示缺陷。
 *
 * 这里用 route 注入 fixture，不依赖任何真实批次。
 */
const fixture = {
  run_id: 'fixture-run',
  run_status: 'succeeded',
  task: {
    task_id: 'fixture-task',
    carrier: 'TEST',
    biztype: 'framework.track_server',
    result: 'CHANGED_DELIVERED',
    attempt: 1,
    steps: [
      { name: 'unified_fixer_agent', status: 'succeeded', attempt: 1, output: { status: 'FIXED', changed_files: ['gathers/TEST/main.py'] } },
      {
        name: 'review_agent', status: 'succeeded', attempt: 1, output: {
          review_kind: 'patch', verdict: 'MANUAL', passed: false, code_verdict: 'PASS',
          semantic_verified: true, delivery_eligible: true,
          coverage: { decision: 'sufficient', reason: '已核对全部改动', reviewed_symbols: ['gathers/TEST/main.py'], missing_checks: [] },
          dimensions: {
            syntax: { status: 'passed', reason: '语法正确', evidence: ['x'] },
            business: { status: 'passed', reason: '源码级语义已核对', evidence: ['x'] },
            scope: { status: 'passed', reason: '仅改授权文件', evidence: ['x'] },
          },
          summary: '补丁方向正确，但采集验证环境未就绪，运行验证未执行。',
          findings: [], required_changes: [], todo: [], unresolved_issues: [],
          validation: {
            replay: { status: 'unverified', outcome: 'environment_unavailable', reason: '采集框架未就绪（NotImplemented），本轮未执行到被测代码' },
            request: { status: 'unverified', outcome: 'environment_unavailable', reason: '采集框架未就绪（NotImplemented），本轮未执行到被测代码' },
          },
        },
      },
    ],
    report: { result: 'CHANGED_DELIVERED', terminal_reason: '已交付' },
  },
};

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1080 } });
const page = await context.newPage();
const errors = [];
page.on('pageerror', error => errors.push(error.message));

const url = '/runs/fixture-run/tasks/fixture-task';
await context.route('**/api/v1/runs/fixture-run/tasks/fixture-task', route => route.fulfill({ json: fixture }));

try {
  await page.goto(`http://127.0.0.1:8000${url}/review`);
  await page.waitForSelector('#review-results', { timeout: 30000 });

  const validation = await page.locator('.review-validation').innerText();
  assert.ok(validation.includes('环境未就绪'), `运行验证区应显示"环境未就绪"，实际：${validation.slice(0, 300)}`);
  assert.ok(!validation.includes('已验证'), '环境未就绪时不得显示"已验证"');

  const decisions = await page.locator('.review-decisions').innerText();
  assert.ok(decisions.includes('环境未就绪'), `决策区应显示"环境未就绪"，实际：${decisions.slice(0, 300)}`);

  if (errors.length) throw new Error(errors.join('\n'));
  console.log('PASS: 采集验证环境未就绪时，运行验证区与决策区都如实显示"环境未就绪"，不显示"已验证"。');
} finally { await browser.close(); }
