/** Browser smoke tests with intercepted fixture APIs; never starts a production run. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const staticMode = process.env.WORKBENCH_STATIC_UI === '1';
const base = staticMode ? 'https://workbench.test' : process.env.WORKBENCH_UI_BASE || 'http://127.0.0.1:5189';
const output = path.resolve('runtime/reviews/2026-10-21/ui');
fs.mkdirSync(output, { recursive: true });
const phases = [
  { key: 'prepare_context', title: '准备上下文', members: ['prepare_source_snapshot', 'repair_branch_preflight', 'prepare_workspace', 'prepare_evidence'], group: true },
  { key: 'locate_agent', title: '根因定位', members: ['locate_agent'], group: false },
  { key: 'validate_changes', title: '变更与验证', members: ['check_changes', 'syntax_regression', 'verify_issue'], group: true },
  { key: 'request_approval', title: '人工审批', members: ['request_approval'], group: false },
  { key: 'finalize_execution', title: '终态报告', members: ['finalize_execution'], group: false },
];
const tasks = [
  { task_id: 'bad', carrier: 'BestVN', result: 'FAILED', terminal_step: 'syntax_regression', terminal_reason: '语法回归失败：main.py 第 12 行存在未闭合的括号。', next_action: '检查失败节点后重试此任务。' },
  { task_id: 'manual', carrier: 'Australia', result: 'MANUAL', terminal_step: 'locate_agent', terminal_reason: '代理通道断开，分析报告已交付。' },
  { task_id: 'success', carrier: 'CarrierOK', result: 'SUCCESS', terminal_step: 'request_approval', terminal_reason: '修复分支已提交并获批，等待手动合并。' },
  { task_id: 'cancelled', carrier: 'StoppedCarrier', result: 'CANCELLED', terminal_step: 'prepare_source_snapshot', terminal_reason: '收到停止请求。' },
].map((task) => ({ biztype: 'framework.track_server', service_name: 'gather.py.track', approval_pending: false,
  steps_count: 3, duration_ms: 4100, attempt: 1, step_statuses: {}, ...task }));
const summary = { run_id: 'demo', status: 'partial_failed', terminal: true, worker_active: false,
  started_at: '2026-10-21T01:28:00Z', finished_at: '2026-10-21T01:34:00Z', task_count: 4,
  succeeded_tasks: 1, failed_tasks: 1, manual_tasks: 1, waiting_tasks: 0 };
let retrying = false;
let approval = false;
let loadingFails = false;
let retryRequests = 0;
let createdRun;

function taskDetail(id) {
  const task = tasks.find((item) => item.task_id === id) || tasks[0];
  const steps = [
    { name: 'check_changes', title: '确定性检查', status: 'succeeded', output: { passed: true }, attempt: 1 },
    { name: 'syntax_regression', title: '语法回归', status: id === 'bad' ? 'failed' : 'succeeded', error: id === 'bad' ? { message: task.terminal_reason } : null, attempt: 1 },
    { name: 'finalize_execution', title: '终态报告', status: 'succeeded', result: task.result, attempt: 1 },
  ];
  if (approval) steps.push({ name: 'request_approval', title: '人工审批', status: 'waiting', attempt: 1 });
  const result = retrying && id === 'bad' ? 'RETRYING' : approval ? 'WAITING_APPROVAL' : task.result;
  return { run_id: 'demo', run_status: retrying ? 'running' : approval ? 'waiting_approval' : 'partial_failed',
    worker_active: retrying, config: {}, artifacts: [], stages: phases,
    task: { ...task, result, approval_pending: approval, steps,
      current_step: retrying ? 'prepare_source_snapshot' : task.terminal_step,
      lifecycle: { status: result.toLowerCase(), current_step: retrying ? 'prepare_source_snapshot' : task.terminal_step,
        terminal_reason: task.terminal_reason, next_action: task.next_action, attempt: retrying ? 2 : 1 },
      report: { result: task.result, terminal_step: task.terminal_step, terminal_reason: task.terminal_reason, elapsed_ms: 4100 } } };
}

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  if (staticMode) {
    const dist = path.resolve('frontend/dist');
    // Serve the production build through browser interception, without opening sockets.
    await page.route('**/*', async (route) => {
      const url = new URL(route.request().url());
      if (url.hostname !== 'workbench.test') return route.abort();
      const file = url.pathname.startsWith('/assets/')
        ? path.resolve(dist, '.' + url.pathname) : path.join(dist, 'index.html');
      if (!file.startsWith(dist + path.sep) || !fs.existsSync(file)) return route.fulfill({ status: 404, body: '' });
      const contentType = file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html';
      return route.fulfill({ contentType, body: fs.readFileSync(file) });
    });
  }
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url());
    const endpoint = url.pathname.replace('/api/v1', '');
    let body;
    if (endpoint === '/health') body = { status: 'ok', service: 'workbench-test', version: '1', runtime_dir_configured: true, source_repo_configured: true };
    else if (endpoint === '/dashboard/summary') body = { active_runs: 0, waiting_approvals: 0, succeeded_24h: 1, failed_24h: 1, success_rate_7d: 0.5, recent_runs: [summary], daily_trend: [] };
    else if (endpoint === '/runs' && route.request().method() === 'POST') { createdRun = route.request().postDataJSON(); body = { run_id: 'demo', status: 'accepted' }; }
    else if (endpoint === '/runs') body = { items: [summary], total: 1, page: 1, page_size: 12 };
    else if (endpoint === '/approvals') body = { items: [], total: 0 };
    else if (endpoint === '/runs/demo') body = { ...summary, config: {}, steps: [], tasks: tasks.map((task) => ({ ...task })), checkpoint: { next_nodes: [], resumable: false }, artifacts: [] };
    else if (endpoint === '/runs/demo/board') body = { run_id: 'demo', summary, stages: phases, tasks, total: tasks.length };
    else if (endpoint.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: 'event: run.snapshot\ndata: {}\n\n' });
    else if (endpoint.endsWith('/retry')) { retrying = true; retryRequests++; body = { message: '已提交 Task 重试', attempt: 2 }; }
    else if (/\/steps\//.test(endpoint)) {
      if (loadingFails) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '测试：步骤详情暂时不可用' }) });
      const detail = taskDetail(endpoint.split('/')[4]);
      body = { step: detail.task.steps.find((step) => step.name === endpoint.split('/')[6]), context: {}, artifacts: [], model_call_ids: [] };
    }
    else if (/^\/runs\/demo\/tasks\//.test(endpoint)) body = taskDetail(endpoint.split('/')[4]);
    else if (endpoint.includes('model-calls')) body = { items: [], total: 0 };
    else body = { items: [], total: 0 };
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  });
  try {
    await page.goto(`${base}/runs`);
    await page.getByRole('button', { name: /发起执行/ }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('spinbutton', { name: /最近多少小时/ }).fill('6');
    assert.equal(await dialog.getByText('日志条数上限', { exact: true }).count(), 0);
    await dialog.getByRole('button', { name: '发起执行', exact: true }).click();
    await page.waitForURL('**/runs/demo');
    assert.equal(createdRun.log_hours, 6);
    assert.equal(createdRun.log_limit, 0);
    assert.equal('log_days' in createdRun, false);
    await page.getByRole('table').waitFor();
    await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('tbody tr').count(), 4);
    await page.getByRole('button', { name: /需要处理/ }).click();
    assert.equal(await page.locator('tbody tr').count(), 1);
    await page.getByRole('button', { name: /全部任务/ }).click();
    await page.getByRole('textbox', { name: '搜索运输商或错误' }).fill('Australia');
    assert.equal(await page.locator('tbody tr').count(), 1);
    await page.getByRole('textbox', { name: '搜索运输商或错误' }).fill('');
    await page.screenshot({ path: path.join(output, 'task-list-desktop.png'), fullPage: true });
    await page.getByRole('button', { name: '排查失败' }).click();
    await page.locator('.task-operations').waitFor();
    await page.waitForLoadState('networkidle');
    assert.equal(await page.getByRole('tab', { name: /执行记录/ }).getAttribute('aria-selected'), 'true');
    await page.locator('.node-rail').getByText('语法回归', { exact: true }).waitFor();
    assert.match(await page.locator('.task-operations').innerText(), /未闭合的括号/);
    await page.screenshot({ path: path.join(output, 'failed-task-desktop.png'), fullPage: true });
    await page.getByRole('tab', { name: '结果与交付' }).click();
    await page.locator('.task-overview').waitFor();
    await page.getByRole('button', { name: '定位失败节点' }).click();
    await page.getByRole('tab', { name: /执行记录/, selected: true }).waitFor();
    await page.getByRole('button', { name: '重试此任务' }).click();
    await page.locator('.task-operations').getByText('重试中', { exact: true }).waitFor();
    assert.equal(retryRequests, 1);
    assert.equal(await page.getByRole('button', { name: '重试此任务' }).count(), 0);
    retrying = false;
    await page.goto(`${base}/runs/demo/tasks/manual`);
    await page.locator('.task-operations').waitFor();
    assert.match(await page.locator('.task-operations').innerText(), /转人工报告/);
    assert.equal(await page.getByRole('button', { name: '重试此任务' }).count(), 0);
    approval = true;
    await page.goto(`${base}/runs/demo/tasks/manual`);
    await page.getByRole('button', { name: '处理审批' }).click();
    await page.waitForURL('**/approvals');
    approval = false;
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${base}/runs/demo`);
    await page.getByRole('table').waitFor();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
    await page.screenshot({ path: path.join(output, 'task-list-mobile.png'), fullPage: true });
    await page.goto(`${base}/runs/demo/tasks/bad`);
    await page.locator('.task-operations').waitFor();
    await page.waitForLoadState('networkidle');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
    await page.screenshot({ path: path.join(output, 'failed-task-mobile.png'), fullPage: true });
    loadingFails = true;
    await page.goto(`${base}/runs/demo/tasks/bad?step=syntax_regression&view=execution`);
    await page.getByText('步骤详情暂时无法加载', { exact: true }).waitFor();
    assert.deepEqual(errors, []);
    console.log('UI smoke passed: hourly full-scan submission, search, filters, failure focus, retry, manual terminal, approval, responsive layout, API error.');
  } catch (error) {
    await page.screenshot({ path: path.join(output, 'smoke-failure.png'), fullPage: true });
    console.error(await page.evaluate(() => [...document.querySelectorAll('body *')].filter((element) => element.getBoundingClientRect().right > window.innerWidth + 1).slice(0, 20).map((element) => ({ tag: element.tagName, class: element.className, right: element.getBoundingClientRect().right }))));
    throw error;
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
