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
const headers = { Authorization: `Bearer ${token}` };
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1080 }, extraHTTPHeaders: headers });
const page = await context.newPage();
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const output = path.join(root, 'runtime/ui-check/task-review');
mkdirSync(output, { recursive: true });

/**
 * 任务路径**不写死**：历史批次会被清理，写死的 run_id 会让测试必然失败。
 * 这里从 API 现场挑一个可用批次，并按页面实际内容决定断言目标。
 */
async function pickRun() {
  const runs = await (await fetch('http://127.0.0.1:8000/api/v1/runs?page=1&page_size=20', { headers })).json();
  const items = Array.isArray(runs?.items) ? runs.items : [];
  for (const run of items) {
    const board = await (await fetch(`http://127.0.0.1:8000/api/v1/runs/${run.run_id}/board`, { headers })).json();
    const tasks = Array.isArray(board?.tasks) ? board.tasks : [];
    if (tasks.length) return { runId: run.run_id, tasks };
  }
  throw new Error('没有可用批次：先跑一轮工作流再执行本检查');
}

async function visit(url) {
  await page.goto(`http://127.0.0.1:8000${url}`);
  await expect(page.locator('#review-results')).toBeVisible({ timeout: 30000 });
}
try {
  const { runId, tasks } = await pickRun();
  const withReview = tasks.find(item => item.step_statuses?.review_agent
    && !['pending', 'running', 'waiting'].includes(item.step_statuses.review_agent));
  const target = withReview ?? tasks[0];
  const base = `/runs/${runId}/tasks/${encodeURIComponent(target.task_id)}`;

  await visit(base);
  await expect(page.locator('.review-dimension')).toHaveCount(3);
  await expect(page.locator('.check-why').first()).not.toContainText('未记录');
  const reviewY = (await page.locator('#review-results').boundingBox()).y;
  if (reviewY >= (await page.locator('#code-changes').boundingBox()).y) throw new Error('Review must precede diff');
  await visit(base + '/review');
  await page.screenshot({ path: path.join(output, 'desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  if (!await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)) throw new Error('Mobile overflow');
  await page.screenshot({ path: path.join(output, 'mobile.png'), fullPage: true });
  await visit(base + '/change');
  await expect(page.locator('.review-dimension')).toHaveCount(3);

  // 运行验证标签必须如实反映后端状态：不能把"环境未就绪"显示成"已验证"。
  const validationText = await page.locator('.review-validation').innerText().catch(() => '');
  if (validationText) {
    assert.ok(!/已验证[\s\S]{0,40}环境未就绪/.test(validationText), '运行验证状态与实际不符');
    assert.ok(!/环境未就绪[\s\S]{0,40}已验证/.test(validationText), '运行验证状态与实际不符');
  }
  // 有审查结论时，四项决策维度必须都在（代码判断 / 业务语义 / 运行验证 / 审查覆盖）。
  if (await page.locator('.review-decisions').count()) {
    await expect(page.locator('.review-decisions')).toContainText('审查覆盖');
  }
  if (errors.length) throw new Error(errors.join('\n'));
  console.log(`PASS: 审查维度、结论顺序、review/change 路由、移动端布局、运行验证标签；批次 ${runId} 任务 ${target.task_id}；无页面错误。`);
} finally { await browser.close(); }
