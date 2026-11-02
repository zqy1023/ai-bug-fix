import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const root = path.resolve(import.meta.dirname, '../../..');
const origin = process.env.WORKBENCH_TEST_ORIGIN || 'http://127.0.0.1:8000';
const token = execFileSync(path.join(root, '.venv/Scripts/python.exe'), ['-c',
  "from config.nacos import nacos_get; print(nacos_get('WORKBENCH_API_TOKEN','').strip())"],
{ cwd: root, encoding: 'utf8' }).trim();
const headers = { Authorization: `Bearer ${token}` };
const output = path.join(root, 'runtime/ui-check/disposition-review');
mkdirSync(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1080 },
  extraHTTPHeaders: headers });
const page = await context.newPage();
const errors = [];
const checks = [];
page.on('pageerror', error => errors.push(error.message));

/**
 * 批次与任务 id **不写死**：历史批次会被清理，写死的 run_id 会让测试必然失败。
 * 这里从 API 现场挑一个批次，再按终态各取一个任务（跳过 / 无改动交付）。
 */
async function pickCases() {
  const runs = await (await fetch(`${origin}/api/v1/runs?page=1&page_size=20`, { headers })).json();
  for (const run of (runs.items || [])) {
    const board = await (await fetch(`${origin}/api/v1/runs/${run.run_id}/board`, { headers })).json();
    const tasks = board.tasks || [];
    const skipped = tasks.find(t => t.result === 'SKIPPED');
    const unchanged = tasks.find(t => t.result === 'UNCHANGED_DELIVERED');
    if (skipped && unchanged) return { run: run.run_id, cases: [[skipped.task_id, 'SKIPPED'], [unchanged.task_id, 'UNCHANGED_DELIVERED']] };
  }
  throw new Error('没有同时含 SKIPPED 与 UNCHANGED_DELIVERED 的批次：先跑一轮工作流再执行本检查');
}

async function visit(run, task, suffix = '') {
  await page.goto(`${origin}/runs/${run}/tasks/${task}${suffix}`);
  await page.locator('#review-results .review-conclusion').waitFor({ timeout: 30000 });
}
try {
  const { run, cases } = await pickCases();
  for (const [task, expected] of cases) {
    const response = await context.request.get(`${origin}/api/v1/runs/${run}/tasks/${task}`, { headers });
    assert.equal(response.status(), 200);
    const detail = await response.json();
    assert.equal(detail.task.result, expected);
    await visit(run, task);
    const review = page.locator('#review-results');
    assert.match(await review.innerText(), /处置审查结果/);
    const decisions = await review.locator('.review-decisions').innerText();
    assert.match(decisions, /处置判断/);
    assert.match(decisions, /运行验证[\s\S]*未验证|环境未就绪/);
    assert.doesNotMatch(decisions, /业务语义[\s\S]*已确认/);
    if (expected === 'SKIPPED') {
      assert.match(await page.locator('#repair-result').innerText(), /已跳过修复与发布/);
    } else {
      assert.match(decisions, /缺陷未修复/);
      assert.match(await page.locator('#repair-result').innerText(), /仍未解决的问题/);
    }
    checks.push({ task, expected, result: 'passed' });
  }
  const task = cases.at(-1)[0];
  await visit(run, task, '/review');
  await page.screenshot({ path: path.join(output, 'desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'mobile overflow');
  await page.screenshot({ path: path.join(output, 'mobile.png'), fullPage: true });

  // Exercise the current schema without changing archived records or executing a task.
  const response = await context.request.get(`${origin}/api/v1/runs/${run}/tasks/${task}`, { headers });
  const fixture = await response.json();
  const review = { ...fixture.task.report.review, review_kind: 'analysis_disposition',
    semantic_verified: false, disposition_verified: true,
    unresolved_issues: ['明确的只读缺陷；空轨迹语义仍待确认'], todo: ['后续补充正常分支回归'] };
  fixture.task.report.review = review;
  for (const step of fixture.task.steps) if (step.name === 'review_agent') step.output = review;
  await page.route(`**/api/v1/runs/${run}/tasks/${task}`, route => route.fulfill({ json: fixture }));
  await visit(run, task, '/review');
  const followups = await page.locator('.review-followups').innerText();
  assert.match(followups, /仍未解决的问题[\s\S]*明确的只读缺陷/);
  assert.match(followups, /TODO · 后续回归建议[\s\S]*后续补充正常分支回归/);
  assert.equal(errors.length, 0, errors.join('\n'));
  writeFileSync(path.join(output, 'receipt.json'), JSON.stringify({ checks, currentSchema: 'passed',
    mobile: 'passed', pageErrors: errors }, null, 2));
  console.log(`PASS: 处置审查页（跳过 / 无改动交付）、处置判断与运行验证状态、未解决问题、当前协议、移动端布局；批次 ${run}；无页面错误。`);
} finally {
  await browser.close();
}
