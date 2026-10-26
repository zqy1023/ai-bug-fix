const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const { mkdirSync, writeFileSync } = require('node:fs');
const { join } = require('node:path');
const origin = process.env.UI_ORIGIN || 'http://127.0.0.1:5173';
const artifacts = process.env.UI_ARTIFACT_DIR || join(require('node:os').tmpdir(), 'stitch-redesign-ui');
const allow = { enabled: true, reason: '' };
const deny = { enabled: false, reason: '当前任务等待人工审批，不允许直接重跑' };
const runId = 'RUN-1025-0830';
const taskId = 'DHL-0042';
const diffRef = { name: 'diff', path: 'artifacts/diff.json' };
const verifyRef = { name: 'verification_report', path: 'artifacts/verification_report.json' };
const passed = (summary) => ({ outcome: 'passed', code: 'VERIFIED', summary });
const record = (name, title, summary, output = {}) => ({ name, title, status: 'succeeded', execution: { status: 'succeeded' }, assessment: passed(summary), summary, output, artifacts: [], started_at: '2026-10-25T01:08:00Z', finished_at: '2026-10-25T01:12:00Z' });
const steps = [
  record('prepare_workspace', '准备工作区', '隔离源码基线已准备'),
  record('locate_agent', '根因定位', '定位到 events 字段缺失', { root_cause: '响应缺少 events 时直接索引字典触发 KeyError。' }),
  record('repair_agent', '代码修复', '增加缺省值处理'),
  { ...record('review_agent', '修复后代码审查 Agent', '三维综合审查通过', {"review_contract_version": 6, "verdict": "PASS", "passed": true, "code_verdict": "PASS", "summary": "代码修复已通过语法、业务修复与影响范围综合审查。", "dimensions": {"syntax": {"status": "passed", "reason": "修改文件语法编译通过。", "evidence": ["carriers/dhl/parser.py:48"]}, "business": {"status": "passed", "reason": "历史异常重放与源码证明缺失字段得到正确处理。", "evidence": ["carriers/dhl/parser.py:48"]}, "scope": {"status": "passed", "reason": "仅修改运输商解析分支，未发现共享逻辑回退。", "evidence": ["carriers/dhl/parser.py:48"]}}, "substeps": [{"name": "check_changes", "title": "范围检查", "status": "succeeded", "summary": "验证证据已取得", "assessment": {"outcome": "passed"}}, {"name": "syntax_regression", "title": "语法检查", "status": "succeeded", "summary": "验证证据已取得", "assessment": {"outcome": "passed"}}, {"name": "plan_validation", "title": "证据计划", "status": "succeeded", "summary": "验证证据已取得", "assessment": {"outcome": "passed"}}, {"name": "verify_issue", "title": "原问题重放", "status": "succeeded", "summary": "验证证据已取得", "assessment": {"outcome": "passed"}}, {"name": "run_regression", "title": "真实请求", "status": "succeeded", "summary": "验证证据已取得", "assessment": {"outcome": "passed"}}, {"name": "summarize_validation", "title": "证据汇总", "status": "succeeded", "summary": "验证证据已取得", "assessment": {"outcome": "passed"}}]}), artifacts: [diffRef, verifyRef] },
  { name: 'request_approval', title: '人工审批', status: 'waiting', execution: { status: 'waiting' }, artifacts: [], started_at: '2026-10-25T01:12:00Z' },
];
const candidate = { phase: 'prepared', candidate_id: 'sample-candidate-a7c32e1', commit: 'a7c32e1', branch: 'codex/fix-dhl-events', local_branch: true, pushed: false };
const task = { task_id: taskId, carrier: 'DHL', biztype: '物流查询', result: 'WAITING_APPROVAL', approval_pending: true, current_step: 'request_approval', steps, attempt: 1, problem: { error: "KeyError: 'events'\nFile carriers/dhl/parser.py, line 48", service_name: 'tracking-service' }, report: { change_summary: '缺失 events 时返回空轨迹，原问题重放与真实请求回归均已通过' } };
const siblingTasks = [task, { task_id: 'DHL-0041', carrier: 'DHL', biztype: '物流查询', result: 'SUCCESS' }, { task_id: 'FEDEX-0043', carrier: 'FedEx', biztype: '轨迹查询', result: 'RUNNING', current_step: 'review_agent' }, { task_id: 'UPS-0044', carrier: 'UPS', biztype: '物流查询', result: 'BLOCKED' }];
const stages = ['locate_agent','repair_agent','review_agent','request_approval','publish_delivery'].map(key => ({key,title:key === 'review_agent' ? '修复后代码审查 Agent' : key,members:[key],group:false}));
const run = { run_id: runId, status: 'running', terminal: false, worker_active: true, started_at: '2026-10-25T00:30:00Z', task_count: 4, succeeded_tasks: 1, failed_tasks: 0, manual_tasks: 0, blocked_tasks: 1, waiting_tasks: 1, inactive_tasks: 0, operator: 'UI acceptance sample', config: { log_hours: 24, log_limit: 500, review_mode: 'human', max_attempts: 3 }, actions: { pause: allow, cancel: allow, resume: deny, retry: deny }, tasks: siblingTasks, steps: [], artifacts: [], run_lifecycle: { status: 'running', current_task_id: 'FEDEX-0043', current_step: 'review_agent' } };
const approval = { approval_id: 'approval-dhl-0042', run_id: runId, task_id: taskId, carrier: 'DHL', biztype: '物流查询', status: 'pending', scope: 'carrier', summary: '响应字段缺失导致解析失败，已生成本地修复候选', changed_files: ['carriers/dhl/parser.py'], change_plan: { scope: 'carrier', rationale: '为上游缺失 events 的响应提供受控缺省处理。', edits: [{ path: 'carriers/dhl/parser.py', action: 'modify', note: '增加空字段兼容处理' }] }, delivery: candidate, actions: { approve: allow, reject: allow }, created_at: '2026-10-25T01:12:00Z' };
const observation = { schema_version: 3, input_delivered: true, parser_calls: [{ business_entry: true, entrypoint: 'DhlParser.parse_tracking_payload' }] };
const verification = { verification: { schema_version: 4 }, replay_comparison: { baseline: { error_type: 'ParseError', error_msg: "KeyError: 'events'", event_count: 0, executed: true, observation, parse_errors: [{ type: 'KeyError', message: 'events', frames: [{ file: 'carriers/dhl/parser.py', line: 48, function: 'parse_tracking_payload' }] }] }, fixed: { error_type: 'Succeed', error_msg: '', event_count: 0, executed: true, observation, parse_errors: [] } }, replay_baseline_output: JSON.stringify({ result: { error: "KeyError: 'events'" } }), replay_output: JSON.stringify({ result: { events: [], tracking_number: 'UI-SAMPLE-001' } }) };
const diff = { files: [{ path: 'carriers/dhl/parser.py', available: true, status: 'modified', before: 'def parse_tracking_payload(payload):\n    events = payload["events"]\n    return events\n', after: 'def parse_tracking_payload(payload):\n    events = payload.get("events") or []\n    if not events:\n        return []\n    return events\n' }] };
(async () => {
  mkdirSync(artifacts, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.UI_BROWSER_CHANNEL || 'msedge' });
  try {
    const page = await browser.newPage();
    const errors = []; const submissions = []; const requests = [];
    page.on('pageerror', (error) => errors.push(error.message));
    let stale = false; let permitApproval = true; let rejectTask = false;
    await page.route('**/api/v1/**', async (route) => {
      const req = route.request(); const url = new URL(req.url()); const path = url.pathname; requests.push(url.pathname + url.search);
      let body = { items: [], total: 0 };
      if (req.method() !== 'GET') {
        assert(path.endsWith('/decision'), `Unexpected mutation: ${path}`); submissions.push(req.postDataJSON()); body = { status: 'resume_requested' };
      } else if (path.endsWith('/health')) body = { status: 'ok', service: 'ui-fixture', version: '6', api_token_required: false };
      else if (path.endsWith('/dashboard/summary')) body = { active_runs: 1, waiting_approvals: 1, failed_24h: 2, succeeded_24h: 8, success_rate_7d: 0, daily_trend: [], recent_runs: [] };
      else if (path.endsWith('/approvals')) body = { items: [{ ...approval, actions: { approve: permitApproval ? allow : deny, reject: allow } }], total: 1 };
      else if (path.endsWith('/runs')) body = { items: [run, { ...run, run_id: 'RUN-1024-0900', status: 'succeeded', terminal: true, task_count: 6, succeeded_tasks: 6, waiting_tasks: 0, blocked_tasks: 0 }, { ...run, run_id: 'RUN-1024-0810', status: 'failed', terminal: true, task_count: 2, succeeded_tasks: 0, failed_tasks: 2, waiting_tasks: 0, blocked_tasks: 0 }], total: 3, page: 1, page_size: 12 };
      else if (path.endsWith('/artifacts/content')) body = { content: url.searchParams.get('path').includes('diff') ? diff : verification };
      else if (path.includes('/steps/')) { const name = decodeURIComponent(path.split('/').at(-1)); body = { step: steps.find((s) => s.name === name) || steps[0], actions: { rerun: deny }, context: { counts: {}, flags: {} }, artifacts: [], model_call_ids: [] }; }
      else if (path.includes('/tasks/')) { const target = decodeURIComponent(path.split('/').at(-1)); body = { run_id: runId, run_status: 'running', worker_active: true, run_terminal: false, stages, actions: { retry: deny }, delivery: candidate, config: run.config, task: target === taskId ? { ...task, steps: steps.map((s) => s.name === 'review_agent' ? { ...s, output: { ...s.output, stale } } : s), result: rejectTask ? 'FAILED' : task.result } : { ...task, ...siblingTasks.find((t) => t.task_id === target), steps }, artifacts: [] }; }
      else if (path.endsWith('/board')) { const filtered = siblingTasks.filter((t) => url.searchParams.get('category') !== 'approval' || t.approval_pending); body = { tasks: filtered.map((t) => ({ ...t, service_name: 'tracking-service', terminal_step: '', terminal_reason: t.result === 'BLOCKED' ? '缺少原始响应，等待补充证据' : '', next_action: '', approval_pending: !!t.approval_pending, steps_count: 9, duration_ms: 42000, step_statuses: {} })), total: filtered.length, all_total: 4, page: 1, page_size: 20, counts: { all: 4, active: 1, approval: 1, success: 1, blocked: 1 }, stages }; }
      else if (path.endsWith(`/runs/${runId}`)) body = run;
      await route.fulfill({ json: body });
    });
    if (!process.env.UI_FLOWS_ONLY) for (const theme of ['light', 'dark']) {
      await page.addInitScript((value) => localStorage.setItem('wb-theme', value), theme);
      for (const width of [1440, 768, 390]) {
        await page.setViewportSize({ width, height: 1000 });
        for (const [name, path] of [['runs', '/runs'], ['run', `/runs/${runId}`], ['task', `/runs/${runId}/tasks/${taskId}`], ['approval', '/approvals']]) {
          await page.goto(origin + path); await page.waitForLoadState('networkidle');
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${theme}/${name}/${width}: page overflow`);
          assert.equal(await page.locator('html').getAttribute('data-theme'), theme);
          if (name === 'task') {
            await page.getByText('修复后代码审查', { exact: true }).waitFor();
            assert.equal(await page.getByRole('button', { name: '重试此任务' }).count(), 0);
          }
          if (name === 'approval') assert.equal(await page.getByRole('button', { name: '批准并交付' }).isEnabled(), true);
          await page.screenshot({ path: join(artifacts, `${name}-${theme}-${width}.png`), fullPage: true });
        }
      }
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(`${origin}/runs/${runId}/tasks/${taskId}`); await page.waitForLoadState('networkidle');
    await page.getByRole('button',{name:'同批次任务',exact:true}).click();
    const search = page.getByRole('textbox', { name: '搜索同批次任务' });
    await search.fill('FedEx'); assert.equal(await page.getByRole('complementary', { name: '同批次任务' }).getByRole('link').count(), 1);
    await page.getByRole('complementary', { name: '同批次任务' }).getByRole('link').click(); await page.waitForURL('**/tasks/FEDEX-0043');
    await page.goto(`${origin}/runs/${runId}/tasks/${taskId}`); await page.getByRole('tab', { name: '代码改动' }).click();
    await page.getByText('parse_tracking_payload(payload):', { exact: false }).first().waitFor();
    assert(requests.some((url) => url.includes('artifacts%2Fdiff.json')));
    stale = true; await page.reload(); await page.getByRole('tab', { name: '修复概览' }).click();
    await page.getByText('证据已过期', { exact: true }).first().waitFor(); stale = false;
    await page.goto(`${origin}/runs/${runId}`); await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: /^待审批\s*1$/ }).click();
    await page.waitForFunction(() => new URL(location.href).searchParams.get('result') === 'approval');
    await page.waitForLoadState('networkidle');
    await page.waitForFunction(()=>document.querySelector('[aria-label="任务状态筛选"] button[aria-pressed="true"]')?.textContent.includes('待审批'));
    assert(requests.some((url) => url.includes('category=approval')));
    permitApproval = false;
    await page.goto(`${origin}/approvals`); await page.waitForLoadState('networkidle');
    assert.equal(await page.getByRole('button', { name: '批准并交付' }).isEnabled(), false); permitApproval = true;
    await page.reload(); await page.getByRole('button', { name: '批准并交付' }).click();
    assert.equal(submissions.length, 0); await page.getByRole('dialog').getByRole('button', { name: /返\s*回/ }).click(); assert.equal(submissions.length, 0);
    await page.getByRole('button', { name: '批准并交付' }).click(); await page.getByRole('dialog').getByRole('button', { name: '确认批准', exact: true }).click();
    await page.waitForURL(`**/tasks/${taskId}`); assert.equal(submissions[0].decision, 'approve');
    await page.goto(`${origin}/approvals`); await page.waitForLoadState('networkidle');
    await page.getByText('补充说明 / 设置驳回原因与回修阶段', { exact: true }).click();
    await page.getByRole('combobox', { name: '驳回原因' }).click(); await page.getByText('问题验证不足', { exact: true }).click();
    await page.getByRole('button', { name: '驳回并回修', exact: true }).click(); await page.getByRole('dialog').getByRole('button', { name: '确认驳回并重跑', exact: true }).click();
    await page.waitForURL(`**/tasks/${taskId}`); assert.equal(submissions[1].decision, 'reject'); assert.equal(submissions[1].retry_target, 'review_agent');
    assert.deepEqual(errors, []);
    const report = { result: 'passed', screenshots: process.env.UI_FLOWS_ONLY ? 0 : 24, themes: ['light', 'dark'], widths: [1440, 768, 390], assertions: ['no page overflow/errors', 'archived verification rendered', 'task sibling navigation/search', 'actual source diff', 'stale evidence cannot pass', 'task filter reaches API', 'server action permissions', 'approval cancellation causes no mutation', 'approval resumes matching task', 'rejection targets validation'], submissions: submissions.map((s) => ({ decision: s.decision, retry_target: s.retry_target })) };
    writeFileSync(join(artifacts, 'report.json'), JSON.stringify(report, null, 2));
    console.log(`PASS: ${process.env.UI_FLOWS_ONLY ? 0 : 24} responsive/theme cases and 10 integration assertions. ${artifacts}`);
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
