/* UI acceptance with explicit API fixtures; never starts real repair/publish jobs. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { mkdirSync } = require('node:fs');
const { join } = require('node:path');
const { tmpdir } = require('node:os');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.UI_BROWSER_CHANNEL || 'msedge' });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const permit = { enabled: true, reason: '' };
    const deny = { enabled: false, reason: '任务正在等待审批' };
    const step = { name: 'plan_validation', title: '确定验证范围', status: 'succeeded',
      execution: { status: 'succeeded' }, assessment: { outcome: 'failed', code: 'ACCEPTANCE_MISSING',
        summary: '修改前验收契约缺失，禁止建立验证计划', findings: ['需要先固定原始问题和基线'] },
      output: {}, artifacts: [], duration_ms: 120 };
    const candidate = { phase: 'prepared', candidate_id: 'fixture-candidate', commit: 'fixture-commit', branch: 'fixture/fix' };
    const stages = [{ key: step.name, title: step.title, members: [step.name], group: false }];
    const task = { run_id: 'fixture', run_status: 'waiting_approval', run_terminal: false, worker_active: false,
      actions: { retry: deny }, delivery: candidate, config: {}, stages,
      task: { task_id: 'fixture', carrier: '界面验收样本', biztype: 'track', result: 'WAITING_APPROVAL',
        approval_pending: true, steps: [step], problem: {}, report: {} }, artifacts: [] };
    const approval = { approval_id: 'fixture:fixture', run_id: 'fixture', task_id: 'fixture', carrier: '界面验收样本',
      biztype: 'track', status: 'pending', scope: 'carrier', summary: '测试本地候选审批', changed_files: [],
      created_at: '2026-10-23T00:00:00Z', delivery: candidate, actions: { approve: permit, reject: permit } };
    let approvalSubmission;
    await page.route('**/api/v1/**', async (route) => {
      const path = new URL(route.request().url()).pathname;
      let body = { items: [], total: 0, page: 1, page_size: 12 };
      if (path.endsWith('/health')) body = { service: 'ui-fixture', version: '6', runtime_dir_configured: true, source_repo_configured: true };
      else if (path.endsWith('/dashboard/summary')) body = { active_runs: 0, waiting_approvals: 1, succeeded_24h: 0, failed_24h: 0, success_rate_7d: 0, daily_trend: [], recent_runs: [] };
      else if (path.endsWith('/approvals')) body = { items: [approval], total: 1 };
      else if (path.includes('/approvals/') && path.endsWith('/decision')) {
        approvalSubmission = route.request().postDataJSON();
        body = { status: 'resume_requested' };
      } else if (path.endsWith('/tasks/fixture/steps/plan_validation')) body = {
        step, actions: { rerun: deny }, artifacts: [], model_call_ids: [], context: { counts: {}, flags: {} } };
      else if (path.endsWith('/tasks/fixture')) body = task;
      else if (path.endsWith('/board')) body = { tasks: [], counts: {}, total: 0, stages };
      else if (path.endsWith('/runs/fixture')) body = { run_id: 'fixture', status: 'waiting_approval', terminal: false,
        actions: { pause: deny, resume: deny, retry: deny, cancel: deny }, config: {}, steps: [], tasks: [], artifacts: [],
        task_count: 1, succeeded_tasks: 0, waiting_tasks: 1, manual_tasks: 0, blocked_tasks: 0, failed_tasks: 0 };
      await route.fulfill({ json: body });
    });
    const origin = process.env.UI_ORIGIN || 'http://127.0.0.1:5179';
    const artifacts = process.env.UI_ARTIFACT_DIR || join(tmpdir(), 'repair-workbench-ui');
    mkdirSync(artifacts, { recursive: true });
    for (const width of [1440, 768, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      for (const [name, path] of [
        ['runs', '/runs'], ['run', '/runs/fixture'],
        ['task', '/runs/fixture/tasks/fixture?view=execution&step=plan_validation'], ['approval', '/approvals'],
      ]) {
        await page.goto(origin + path);
        await page.waitForLoadState('networkidle');
        if (name === 'task') {
          await page.getByRole('region', { name: '步骤判定' }).getByText('业务未通过', { exact: true }).waitFor();
          assert.equal(await page.getByRole('button', { name: '重试本步骤' }).count(), 0);
        }
        if (name === 'approval') {
          await page.getByText('fixture-commit', { exact: true }).waitFor();
          assert.equal(await page.getByRole('button', { name: '批准并交付' }).isEnabled(), true);
        }
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${name}: overflow at ${width}`);
        await page.screenshot({ path: join(artifacts, `${name}-${width}.png`), fullPage: true });
      }
    }
    await page.getByRole('button', { name: '批准并交付' }).click();
    const modal = page.getByRole('dialog');
    await modal.getByRole('button', { name: '确认批准' }).click();
    await page.waitForURL('**/runs/fixture/tasks/fixture');
    assert.equal(approvalSubmission.decision, 'approve');
    assert.deepEqual(errors, []);
    console.log(`PASS: 4 workspaces × 3 widths, formal assessment, approval submission, no page errors/overflow. Screenshots: ${artifacts}`);
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
