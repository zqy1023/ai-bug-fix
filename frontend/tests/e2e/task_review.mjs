import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '../../..');
function expect(locator) {
  return {
    toBeVisible: options => locator.waitFor({ state: 'visible', ...options }),
    toHaveCount: async count => assert.equal(await locator.count(), count),
    toContainText: async value => assert.ok((await locator.innerText()).includes(value), `Expected text: ${value}`),
    not: { toContainText: async value => assert.ok(!(await locator.innerText()).includes(value), `Unexpected text: ${value}`) },
  };
}
const token = execFileSync(path.join(root, '.venv/Scripts/python.exe'), ['-c',
  "from config.nacos import nacos_get; print(nacos_get('WORKBENCH_API_TOKEN','').strip())"], { cwd: root, encoding: 'utf8' }).trim();
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1080 }, extraHTTPHeaders: { Authorization: `Bearer ${token}` } });
const page = await context.newPage();
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const output = path.join(root, 'runtime/ui-check/task-review');
mkdirSync(output, { recursive: true });
const coupang = '/runs/13ccd34a93e34f988f667e9909cb6083/tasks/Coupang__framework.track_server__e85f521c1a42';
const brazil = '/runs/4fbfd3a454674bb2a6ae3264066f24e9/tasks/Brazil__framework.track_server__e883826d2eb5';
async function visit(url) {
  await page.goto(`http://127.0.0.1:8000${url}`);
  await expect(page.locator('#review-results')).toBeVisible({ timeout: 30000 });
}
try {
  await visit(coupang);
  await expect(page.locator('.review-dimension')).toHaveCount(3);
  await expect(page.locator('.check-why').first()).not.toContainText('未记录');
  await expect(page.locator('.review-symbols li').first()).toBeVisible();
  const reviewY = (await page.locator('#review-results').boundingBox()).y;
  if (reviewY >= (await page.locator('#code-changes').boundingBox()).y) throw new Error('Review must precede diff');
  await visit(coupang + '/review');
  await page.screenshot({ path: path.join(output, 'desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  if (!await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)) throw new Error('Mobile overflow');
  await page.screenshot({ path: path.join(output, 'mobile.png'), fullPage: true });
  await visit(coupang + '/change');
  await expect(page.locator('.review-dimension')).toHaveCount(3);
  await visit(brazil + '/review');
  await expect(page.locator('.review-decisions')).toContainText('未验证');
  await expect(page.locator('.review-validation')).toContainText('未验证');
  await expect(page.locator('.review-followups')).toContainText('TODO');
  await visit('/runs/13ccd34a93e34f988f667e9909cb6083/tasks/Brazil__framework.track_server__e883826d2eb5/review');
  await expect(page.locator('.review-empty')).toContainText('审查未执行');
  const espoirer = '/runs/13ccd34a93e34f988f667e9909cb6083/tasks/ESPoirer__common.base_func__829669d73bcd';
  await visit(espoirer);
  await expect(page.locator('#problem-analysis')).toContainText('open_process');
  await expect(page.locator('.task-stage.fail')).toContainText('失败');
  await page.goto(`http://127.0.0.1:8000${espoirer}/audit/steps`);
  await page.locator('.tl-item').filter({ hasText: 'open_process' }).waitFor();
  await expect(page.locator('.tl-item').filter({ hasText: 'open_process' })).toContainText('模型调用失败');
  if (errors.length) throw new Error(errors.join('\n'));
  console.log('PASS: Coupang/Brazil complete reviews, overview order, review/change routes, empty review, mobile layout, ESPoirer startup failure in analysis/progress/audit; no page errors.');
} finally { await browser.close(); }
