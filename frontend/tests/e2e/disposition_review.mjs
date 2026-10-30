import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const root = path.resolve(import.meta.dirname, '../../..');
const origin = process.env.WORKBENCH_TEST_ORIGIN || 'http://127.0.0.1:8000';
const run = process.env.WORKBENCH_TEST_RUN || 'bab1219038e84da68be17cfaa778d2b6';
const token = execFileSync(path.join(root, '.venv/Scripts/python.exe'), ['-c',
  "from config.nacos import nacos_get; print(nacos_get('WORKBENCH_API_TOKEN','').strip())"],
{ cwd: root, encoding: 'utf8' }).trim();
const output = path.join(root, 'runtime/ui-check/disposition-review');
mkdirSync(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1080 },
  extraHTTPHeaders: { Authorization: `Bearer ${token}` } });
const page = await context.newPage();
const errors = [];
const checks = [];
page.on('pageerror', error => errors.push(error.message));
const cases = [
  ['ACSWorldwideExpress__framework.track_server__b44363514170', 'SKIPPED'],
  ['CuPostKr__common.base_func__95c3ef3dffa7', 'SKIPPED'],
  ['Intras__common.base_func__9412c3667302', 'SKIPPED'],
  ['EmuExpress__common.base_func__6d7de0b4f907', 'UNCHANGED_DELIVERED'],
  ['SmithLogistics__common.base_func__01856dc76a57', 'UNCHANGED_DELIVERED'],
  ['TWSExpress__common.base_func__8e5813e051cf', 'UNCHANGED_DELIVERED'],
];
async function visit(task, suffix = '') {
  await page.goto(`${origin}/runs/${run}/tasks/${task}${suffix}`);
  await page.locator('#review-results .review-conclusion').waitFor({ timeout: 30000 });
}
try {
  for (const [task, expected] of cases) {
    const response = await context.request.get(`${origin}/api/v1/runs/${run}/tasks/${task}`);
    assert.equal(response.status(), 200);
    const detail = await response.json();
    assert.equal(detail.task.result, expected);
    await visit(task);
    const review = page.locator('#review-results');
    assert.match(await review.innerText(), /处置审查结果/);
    const decisions = await review.locator('.review-decisions').innerText();
    assert.match(decisions, /处置判断/);
    assert.match(decisions, /运行验证[\s\S]*未验证/);
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
  await visit(task, '/review');
  await page.screenshot({ path: path.join(output, 'desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'mobile overflow');
  await page.screenshot({ path: path.join(output, 'mobile.png'), fullPage: true });

  // Exercise the current schema without changing archived records or executing a task.
  const response = await context.request.get(`${origin}/api/v1/runs/${run}/tasks/${task}`);
  const fixture = await response.json();
  const review = { ...fixture.task.report.review, review_kind: 'analysis_disposition',
    semantic_verified: false, disposition_verified: true,
    unresolved_issues: ['明确的只读缺陷；空轨迹语义仍待确认'], todo: ['后续补充正常分支回归'] };
  fixture.task.report.review = review;
  for (const step of fixture.task.steps) if (step.name === 'review_agent') step.output = review;
  await page.route(`**/api/v1/runs/${run}/tasks/${task}`, route => route.fulfill({ json: fixture }));
  await visit(task, '/review');
  const followups = await page.locator('.review-followups').innerText();
  assert.match(followups, /仍未解决的问题[\s\S]*明确的只读缺陷/);
  assert.match(followups, /TODO · 后续回归建议[\s\S]*后续补充正常分支回归/);
  assert.equal(errors.length, 0, errors.join('\n'));
  writeFileSync(path.join(output, 'receipt.json'), JSON.stringify({ checks, currentSchema: 'passed',
    mobile: 'passed', pageErrors: errors }, null, 2));
  console.log('PASS: six real task pages, distinct disposition and runtime states, unresolved defects, current schema, mobile layout; no page errors.');
} finally {
  await browser.close();
}
