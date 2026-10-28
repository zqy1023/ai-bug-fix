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
  record('unified_fixer_agent', '统一分析修复模型', '定位到 events 字段缺失并完成受限修补', { root_cause: '响应缺少 events 时直接索引字典触发 KeyError。', changed_files: ['carriers/dhl/parser.py'], summary: '为缺失 events 的响应增加受控缺省处理。' }),
  { ...record('review_agent', '审查模型', '三维综合审查通过', {"review_contract_version": 6, "verdict": "PASS", "passed": true, "code_verdict": "PASS", "summary": "代码修复已通过语法、业务修复与影响范围综合审查。", "dimensions": {"syntax": {"status": "passed", "reason": "修改文件语法编译通过。", "evidence": ["carriers/dhl/parser.py:48"]}, "business": {"status": "passed", "reason": "历史异常重放与源码证明缺失字段得到正确处理。", "evidence": ["carriers/dhl/parser.py:48"]}, "scope": {"status": "passed", "reason": "仅修改运输商解析分支，未发现共享逻辑回退。", "evidence": ["carriers/dhl/parser.py:48"]}}, "substeps": [{"name": "check_changes", "title": "范围检查", "status": "succeeded", "summary": "验证证据已取得", "assessment": {"outcome": "passed"}}, {"name": "syntax_regression", "title": "语法检查", "status": "succeeded", "summary": "验证证据已取得", "assessment": {"outcome": "passed"}}, {"name": "plan_validation", "title": "证据计划", "status": "succeeded", "summary": "验证证据已取得", "assessment": {"outcome": "passed"}}, {"name": "verify_issue", "title": "原问题重放", "status": "succeeded", "summary": "验证证据已取得", "assessment": {"outcome": "passed"}}, {"name": "run_regression", "title": "真实请求", "status": "succeeded", "summary": "验证证据已取得", "assessment": {"outcome": "passed"}}, {"name": "summarize_validation", "title": "证据汇总", "status": "succeeded", "summary": "验证证据已取得", "assessment": {"outcome": "passed"}}]}), artifacts: [diffRef, verifyRef] },
  { name: 'request_approval', title: '人工审批', status: 'waiting', execution: { status: 'waiting' }, artifacts: [], started_at: '2026-10-25T01:12:00Z' },
];
const candidate = { phase: 'prepared', candidate_id: 'sample-candidate-a7c32e1', commit: 'a7c32e1', branch: 'codex/fix-dhl-events', local_branch: true, pushed: false };
const fullMessage = '  原始请求 req:{"carrier":"DHL","tracking_number":"UI-001"}\n' + 'Traceback: 完整异常上下文\n'.repeat(80) + "error: KeyError: events\n  原文结束 END-OF-MESSAGE  ";
const task = { task_id: taskId, carrier: 'DHL', biztype: '物流查询', result: 'WAITING_APPROVAL', approval_pending: true, current_step: 'request_approval', steps, attempt: 1, problem: { message: fullMessage, carrier: 'DHL', biztype: '物流查询', error: "KeyError: 'events'\nFile carriers/dhl/parser.py, line 48", service_name: 'tracking-service' }, report: { change_summary: '缺失 events 时返回空轨迹，原问题重放与真实请求回归均已通过' } };
const siblingTasks = [task, { task_id: 'DHL-0041', carrier: 'DHL', biztype: '物流查询', result: 'SUCCESS' }, { task_id: 'FEDEX-0043', carrier: 'FedEx', biztype: '轨迹查询', result: 'RUNNING', current_step: 'review_agent' }, { task_id: 'UPS-0044', carrier: 'UPS', biztype: '物流查询', result: 'BLOCKED' }];
const stages = [
  { key: 'prepare_context', title: '准备上下文', members: ['prepare_workspace'], group: true },
  { key: 'claim_repair_slot', title: '资格领取确认', members: ['claim_repair_slot'], group: false },
  { key: 'unified_fixer_agent', title: '统一分析修复模型', members: ['unified_fixer_agent'], group: false },
  { key: 'review_agent', title: '修复后代码审查 Agent', members: ['review_agent'], group: false },
  { key: 'generate_report', title: '汇总生成报告', members: ['generate_report'], group: false },
  { key: 'submit_changes', title: '提交候选分支', members: ['submit_changes'], group: false },
  { key: 'request_approval', title: '人工审批', members: ['request_approval'], group: false },
];
const run = { run_id: runId, status: 'running', terminal: false, worker_active: true, started_at: '2026-10-25T00:30:00Z', task_count: 4, succeeded_tasks: 1, failed_tasks: 0, manual_tasks: 0, blocked_tasks: 1, waiting_tasks: 1, inactive_tasks: 0, operator: 'UI acceptance sample', config: { log_hours: 24, log_limit: 500, review_mode: 'human', max_attempts: 3 }, actions: { pause: allow, cancel: allow, resume: deny, retry: deny }, tasks: siblingTasks, steps: [], artifacts: [], run_lifecycle: { status: 'running', current_task_id: 'FEDEX-0043', current_step: 'review_agent' } };
const approval = { approval_id: 'approval-dhl-0042', run_id: runId, task_id: taskId, carrier: 'DHL', biztype: '物流查询', status: 'pending', scope: 'carrier', summary: '响应字段缺失导致解析失败，已生成本地修复候选', changed_files: ['carriers/dhl/parser.py'], change_plan: { scope: 'carrier', rationale: '为上游缺失 events 的响应提供受控缺省处理。', edits: [{ path: 'carriers/dhl/parser.py', action: 'modify', note: '增加空字段兼容处理' }] }, delivery: candidate, actions: { approve: allow, reject: allow }, created_at: '2026-10-25T01:12:00Z' };
if (process.env.UI_REVIEW_ONLY) {
  candidate.phase = 'published'; candidate.pushed = true; candidate.local_branch = false;
  approval.changed_files = [];
  const reviewOutput = steps.find(step => step.name === 'review_agent').output;
  delete reviewOutput.substeps;
  reviewOutput.diff_files = [];
  reviewOutput.evidence_version = {baseline_id:'baseline',patch_id:'patch'};
  steps.push(...['check_changes', 'syntax_regression', 'plan_validation'].map(name => record(name, name, '历史脚本记录')));
}
const reviewCall = {model_call_id:'review-call',agent_role:'review',attempt:1,status:'succeeded',model:'test',tool_call_count:0};
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
    let stale = false; let permitApproval = true; let rejectTask = false; let logMode = 'direct'; let activityPhase = 'repair';
    await page.route('**/api/v1/**', async (route) => {
      const req = route.request(); const url = new URL(req.url()); const path = url.pathname; requests.push(url.pathname + url.search);
      let body = { items: [], total: 0 };
      if (req.method() !== 'GET') {
        assert(path.endsWith('/decision'), `Unexpected mutation: ${path}`); submissions.push(req.postDataJSON()); body = { status: 'resume_requested' };
      } else if (path.endsWith('/health')) body = { status: 'ok', service: 'ui-fixture', version: '6', api_token_required: false };
      else if (process.env.UI_REVIEW_ONLY && path.endsWith('/model-calls')) body = {items:[reviewCall],total:1};
      else if (process.env.UI_REVIEW_ONLY && path.includes('/agent-calls/')) body = {call:reviewCall,prompt_snapshot:{system_prompt_common:'审查规则',task_prompt:'实际审查提示词 UI-REVIEW-PROMPT',chars:{},attempt:1,model:'test'},events:[{type:'assistant',text:'已读取基线和当前源码 UI-REVIEW-OUTPUT',final:true}],event_total:1};
      else if (path.endsWith('/dashboard/summary')) body = { active_runs: 1, waiting_approvals: 1, failed_24h: 2, succeeded_24h: 8, success_rate_7d: 0, daily_trend: [], recent_runs: [] };
      else if (path.endsWith('/approvals')) body = { items: [{ ...approval, actions: { approve: permitApproval ? allow : deny, reject: allow } }], total: 1 };
      else if (path.endsWith('/runs')) body = { items: [run, { ...run, run_id: 'RUN-1024-0900', status: 'succeeded', terminal: true, task_count: 6, succeeded_tasks: 6, waiting_tasks: 0, blocked_tasks: 0 }, { ...run, run_id: 'RUN-1024-0810', status: 'failed', terminal: true, task_count: 2, succeeded_tasks: 0, failed_tasks: 2, waiting_tasks: 0, blocked_tasks: 0 }], total: 3, page: 1, page_size: 12 };
      else if (path.endsWith('/artifacts/content') && url.searchParams.get('path') === 'artifacts/normalized_records.json') body = { content: { records: logMode === 'archive' ? [{problem: task.problem}] : [] } };
      else if (path.endsWith('/artifacts/content')) body = { content: url.searchParams.get('path').includes('diff') ? diff : verification };
      else if (path.includes('/steps/')) { const name = decodeURIComponent(path.split('/').at(-1)); body = { step: steps.find((s) => s.name === name) || steps[0], actions: { rerun: deny }, context: { counts: {}, flags: {} }, artifacts: [], model_call_ids: [] }; }
      else if (path.includes('/tasks/')) { const target = decodeURIComponent(path.split('/').at(-1)); body = { run_id: runId, run_status: 'running', worker_active: true, run_terminal: false, stages, actions: { retry: deny }, delivery: candidate, config: run.config, task: target === taskId ? { ...task, steps: steps.map((s) => s.name === 'review_agent' ? { ...s, output: { ...s.output, stale } } : s), result: rejectTask ? 'FAILED' : task.result } : { ...task, ...siblingTasks.find((t) => t.task_id === target), steps }, artifacts: [] }; }
      else if (path.endsWith('/board')) { const filtered = siblingTasks.filter((t) => url.searchParams.get('category') !== 'approval' || t.approval_pending); body = { tasks: filtered.map((t) => ({ ...t, service_name: 'tracking-service', terminal_step: '', terminal_reason: t.result === 'BLOCKED' ? '缺少原始响应，等待补充证据' : '', next_action: '', approval_pending: !!t.approval_pending, steps_count: 9, duration_ms: 42000, step_statuses: {} })), total: filtered.length, all_total: 4, page: 1, page_size: 20, counts: { all: 4, active: 1, approval: 1, success: 1, blocked: 1 }, stages }; }
      else if (path.endsWith(`/runs/${runId}`)) body = run;
      if (path.includes('/tasks/') && !path.includes('/steps/') && body.task && logMode !== 'direct') {
        body.task = { ...body.task, problem: { ...body.task.problem, message: '', raw_error: 'EXTRACTED-ONLY-SHOULD-NOT-BE-MESSAGE' } };
      }
      if (process.env.UI_FAILURE_PREVIEW && body.task) {
        const reason = '后台工作流异常（错误类型：ValidationError: result[literal_error]）';
        body = {...body, run_status:'failed', worker_active:false, run_terminal:true,
          run_error:{status:'FAILED',message:reason,terminal_reason:reason},
          task:{...body.task,result:'FAILED',approval_pending:false,current_step:'unified_fixer_agent',
            lifecycle:{status:'failed',terminal_reason:reason},
            steps:body.task.steps.map(step=>step.name==='unified_fixer_agent'?{...step,status:'failed',output:{root_cause:reason,summary:reason}}:step)}};
      }
      if (process.env.UI_ACTIVITY_ONLY && path.endsWith(`/runs/${runId}`)) {
        const review = activityPhase !== 'repair';
        const current = review ? 'review_agent' : 'unified_fixer_agent';
        body = { ...run, status: activityPhase === 'paused' ? 'paused' : 'running', worker_active: activityPhase !== 'paused',
          run_lifecycle: { current_task_id: 'FEDEX-0043', current_step: current, progress: {step: 'normalize_error', message: '旧的日志扫描信息不应显示', counts: {} } },
          tasks: [{ task_id: 'FEDEX-0043', carrier: 'FedEx', biztype: '轨迹查询', result: 'RUNNING', current_step: current, attempt: 1,
            lifecycle: {status: activityPhase === 'paused' ? 'paused' : 'running', current_step: current, updated_at: new Date().toISOString()},
            steps: [{ name: 'prepare_workspace', title: '准备工作区', status: 'succeeded', attempt: 1 },
              { name: current, title: review ? '修复后代码审查 Agent' : '统一分析修复 Agent', status: 'running', attempt: 1,
                started_at: new Date(Date.now() - 65000).toISOString(), summary: review ? '正在检查补丁影响范围' : '正在定位异常分支' }] }] };
        if (['source', 'logs', 'unlimited', 'zero', 'generic', 'completed'].includes(activityPhase)) {
          const source = activityPhase === 'source';
          const current = source ? 'sync_source_repo' : activityPhase === 'generic' ? 'identify_carrier' : 'normalize_error';
          body = { ...run, tasks: [], run_lifecycle: { current_step: current,
            progress: { step: activityPhase === 'generic' ? 'sync_source_repo' : current,
              message: source ? '首次克隆 · 第 1/3 次尝试' : '正在读取并规范化 ES 日志',
              counts: source ? {downloaded_bytes: 15728640, elapsed_seconds: 12, timeout_seconds: 600}
                : {es_hits: activityPhase === 'zero' ? 0 : 1200, parsed: activityPhase === 'zero' ? 0 : 1000,
                  unparseable: activityPhase === 'zero' ? 0 : 200, record_limit: activityPhase === 'unlimited' ? 0 : 5000} },
            progress_at: '2026-10-28T01:02:03Z' },
            steps: [{name: current, title: source ? '同步源码' : current === 'normalize_error' ? '拉取日志' : '识别运输商',
              status: activityPhase === 'completed' ? 'succeeded' : 'running', started_at: new Date(Date.now() - 12000).toISOString()}] };
        }
      }
      await route.fulfill({ json: body });
    });
    if (process.env.UI_REVIEW_ONLY) {
      await page.setViewportSize({width:1440,height:1000});
      await page.goto(`${origin}/runs/${runId}/tasks/${taskId}`); await page.waitForLoadState('networkidle');
      await page.getByRole('tab',{name:/执行时间线 · 4/}).click();
      for (const name of ['check_changes','syntax_regression','plan_validation']) {
        assert.equal(await page.getByRole('button',{name:new RegExp(name)}).count(),0,`${name} must not be a workflow node`);
      }
      assert.equal(await page.getByText(/历史审查内部记录|审查内部证据未回传/).count(),0);
      await page.screenshot({path:join(artifacts,'agent-review-timeline.png'),fullPage:true});
      await page.getByRole('button',{name:/修复后代码审查 Agent.*已完成/}).first().click();
      await page.getByText('实际审查提示词 UI-REVIEW-PROMPT',{exact:true}).waitFor();
      await page.getByText('已读取基线和当前源码 UI-REVIEW-OUTPUT',{exact:false}).first().waitFor();
      await page.screenshot({path:join(artifacts,'agent-review-trace.png'),fullPage:true});
      await page.keyboard.press('Escape');
      await page.getByRole('tab',{name:'验证证据',exact:true}).click();
      await page.getByRole('list',{name:'代码审查三个维度'}).waitFor();
      assert.equal(await page.getByText(/重放对比|内部证据未回传|变更门禁/).count(),0);
      await page.goto(`${origin}/approvals`); await page.waitForLoadState('networkidle');
      assert.equal(await page.getByLabel('确认保留',{exact:true}).isEnabled(),true);
      await page.screenshot({path:join(artifacts,'published-report-approval.png'),fullPage:true});
      await page.getByLabel('确认保留',{exact:true}).click();
      await page.getByRole('dialog').getByRole('button',{name:/返\s*回/}).click();
      assert.equal(submissions.length,0);
      assert.deepEqual(errors,[]);
      console.log(`PASS: retired script records excluded, Agent dimensions and prompt/output visible, published report approval enabled. ${artifacts}`);
      return;
    }
    if (process.env.UI_FAILURE_PREVIEW) {
      for (const width of [2560,1440,390]) {
        await page.setViewportSize({width,height:1100});
        await page.goto(`${origin}/runs/${runId}/tasks/${taskId}`);await page.waitForLoadState('networkidle');
        const log = page.getByLabel('完整异常 message',{exact:true});await log.waitFor();
        assert.equal(await log.textContent(),fullMessage);
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
        if(width>=1440) assert((await log.boundingBox()).y<650,'Exception must appear in first viewport');
        await page.screenshot({path:join(artifacts,`task-failed-${width}.png`),fullPage:true});
      }
      assert.deepEqual(errors,[]);console.log('PASS: failed task at 2560/1440/390, exception visible in first viewport');return;
    }
    if (process.env.UI_ACTIVITY_ONLY) {
      await page.setViewportSize({width:1440,height:1000});
      await page.goto(origin + '/runs'); await page.waitForLoadState('networkidle');
      const activity = page.getByLabel('当前执行进度', {exact:true});
      await activity.getByText('统一分析修复 Agent', {exact:true}).waitFor();
      assert((await activity.textContent()).includes('FedEx · 轨迹查询'));
      assert((await activity.textContent()).includes('本次已记录步骤：1 / 2 已完成'));
      assert((await activity.textContent()).includes('节点耗时'));
      assert(!(await activity.textContent()).includes('旧的日志扫描信息'));
      await page.screenshot({path:join(artifacts,'run-activity-repair.png'),fullPage:true});
      activityPhase = 'review';
      await activity.getByText('修复后代码审查 Agent', {exact:true}).waitFor({timeout:15000});
      assert((await activity.textContent()).includes('正在检查补丁影响范围'));
      activityPhase = 'paused';
      await activity.getByText('执行已暂停', {exact:true}).first().waitFor({timeout:15000});
      assert.equal(await activity.getByText('执行中', {exact:true}).count(),0);
      activityPhase = 'source';
      const progress = activity.getByLabel('步骤执行进度', {exact:true});
      await progress.getByText('已下载 15.0 MiB', {exact:false}).waitFor({timeout:15000});
      assert((await progress.textContent()).includes('已用 12 秒'));
      assert((await progress.textContent()).includes('超时设置 600 秒'));
      await activity.getByText('下载中', {exact:true}).waitFor();
      await page.screenshot({path:join(artifacts,'run-activity-source.png'),fullPage:true});
      activityPhase = 'logs';
      await progress.getByText('已拉取 1,200 条', {exact:false}).waitFor({timeout:15000});
      assert((await progress.textContent()).includes('已解析 1,000 条'));
      assert((await progress.textContent()).includes('无法解析 200 条'));
      assert((await progress.textContent()).includes('拉取上限 5,000 条'));
      assert(!(await progress.textContent()).includes('%'));
      assert(!(await activity.textContent()).includes('已下载'));
      await page.screenshot({path:join(artifacts,'run-activity-logs.png'),fullPage:true});
      activityPhase = 'unlimited';
      await page.reload(); await page.waitForLoadState('networkidle');
      assert(!(await progress.textContent()).includes('上限'));
      activityPhase = 'zero';
      await page.reload(); await page.waitForLoadState('networkidle');
      assert((await progress.textContent()).includes('已拉取 0 条'));
      activityPhase = 'generic';
      await page.reload(); await page.waitForLoadState('networkidle');
      await activity.getByText('执行中', {exact:true}).first().waitFor();
      assert.equal(await progress.count(),0);
      assert(!(await activity.textContent()).includes('首次克隆'));
      activityPhase = 'completed';
      await page.reload(); await page.waitForLoadState('networkidle');
      await activity.getByText('节点已完成', {exact:true}).waitFor();
      assert.equal(await progress.count(),0);
      assert.deepEqual(errors, []);
      console.log('PASS: task/node, polling, pause, source download, log counts, unlimited/zero counts, generic fallback, completed step, stale progress suppressed');
      return;
    }
    if (process.env.UI_DETAIL_ONLY) {
      for (const theme of ['light', 'dark']) {
        await page.addInitScript(value => localStorage.setItem('wb-theme', value), theme);
        for (const width of [1440, 768, 390]) {
          await page.setViewportSize({ width, height: 1000 });
          for (const [name, path] of [['task', `/runs/${runId}/tasks/${taskId}`], ['approval', '/approvals']]) {
            await page.goto(origin + path); await page.waitForLoadState('networkidle');
            const logs = page.getByLabel('完整异常 message', { exact: true });
            await logs.first().waitFor();
            for (const log of await logs.all()) assert.equal(await log.textContent(), fullMessage);
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${theme}/${name}/${width}: overflow`);
            await page.screenshot({ path: join(artifacts, `${name}-${theme}-${width}.png`), fullPage: true });
          }
        }
      }
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.goto(`${origin}/runs/${runId}/tasks/${taskId}?view=timeline&step=unified_fixer_agent`);
      await page.waitForLoadState('networkidle');
      const dialog = page.getByRole('dialog');
      if (await dialog.count()) await page.keyboard.press('Escape');
      await page.getByRole('tab', { name: '根因分析', exact: true }).click();
      await page.getByLabel('完整异常 message', { exact: true }).waitFor();
      assert.equal(new URL(page.url()).searchParams.has('step'), false);
      logMode = 'archive';
      await page.goto(`${origin}/runs/${runId}/tasks/${taskId}`); await page.waitForLoadState('networkidle');
      assert.equal(await page.getByLabel('完整异常 message', { exact: true }).textContent(), fullMessage);
      logMode = 'missing';
      await page.reload(); await page.waitForLoadState('networkidle');
      await page.getByText('这条历史记录未保存完整 message，无法还原原始异常全文。', { exact: true }).waitFor();
      assert.equal(await page.getByLabel('完整异常 message', { exact: true }).count(), 0);
      assert.deepEqual(errors, []);
      console.log(`PASS: 12 layout/theme cases, complete message preserved, tab navigation. ${artifacts}`);
      return;
    }
    if (!process.env.UI_FLOWS_ONLY) for (const theme of ['light', 'dark']) {
      await page.addInitScript((value) => localStorage.setItem('wb-theme', value), theme);
      for (const width of [1440, 768, 390]) {
        await page.setViewportSize({ width, height: 1000 });
        for (const [name, path] of [['runs', '/runs'], ['run', `/runs/${runId}`], ['task', `/runs/${runId}/tasks/${taskId}`], ['approval', '/approvals']]) {
          await page.goto(origin + path); await page.waitForLoadState('networkidle');
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${theme}/${name}/${width}: page overflow`);
          assert.equal(await page.locator('html').getAttribute('data-theme'), theme);
          if (name === 'task') {
            // 常驻结论条在任一页签下都在：指标位与结论文案必须同时可见。
            await page.getByRole('region', { name: '任务结论' }).waitFor();
            assert.equal(await page.getByRole('button', { name: '重试此任务' }).count(), 0);
            for (const tab of ['根因分析', '代码改动', '验证证据', 'Git 交付', '执行时间线']) {
              assert.equal(await page.getByRole('tab', { name: new RegExp(`^${tab}`) }).count(), 1, `${theme}/${width}: missing tab ${tab}`);
            }
            // 侧栏常驻：默认分区下任务元数据必须可见（旧版仅非执行页签才有）。
            await page.getByRole('complementary', { name: '任务元数据' }).waitFor();
          }
          if (name === 'approval') assert.equal(await page.getByLabel('确认保留', { exact: true }).isEnabled(), true);
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
    // 逐行差异在抽屉里按需加载；文件行与“查看代码差异”都能打开同一个产物。
    await page.getByRole('button', { name: '查看代码差异' }).click();
    await page.getByText('parse_tracking_payload(payload):', { exact: false }).first().waitFor();
    assert(requests.some((url) => url.includes('artifacts%2Fdiff.json')));
    await page.keyboard.press('Escape');
    // 指标位与页签等价：点 Hero 的证据指标应直接落到对应分区。
    await page.goto(`${origin}/runs/${runId}/tasks/${taskId}`);
    await page.getByRole('region', { name: '任务结论' }).getByRole('button', { name: /问题验证/ }).click();
    await page.getByText('修复前后重放对比', { exact: true }).waitFor();
    // 进入执行时间线后侧栏仍常驻，且步骤抽屉可从时间线打开。
    await page.getByRole('tab', { name: /执行时间线/ }).click();
    await page.getByRole('complementary', { name: '任务元数据' }).waitFor();
    await page.getByRole('button', { name: /分析模型/ }).first().click();
    await page.getByRole('dialog').waitFor();
    await page.getByRole('dialog').getByRole('button', { name: '关闭' }).click();
    stale = true; await page.reload(); await page.getByRole('tab', { name: '验证证据' }).click();
    await page.getByText('证据已过期', { exact: true }).first().waitFor(); stale = false;
    await page.goto(`${origin}/runs/${runId}`); await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: /^待审批\s*1$/ }).click();
    await page.waitForFunction(() => new URL(location.href).searchParams.get('result') === 'approval');
    await page.waitForLoadState('networkidle');
    await page.waitForFunction(()=>document.querySelector('[aria-label="任务状态筛选"] button[aria-pressed="true"]')?.textContent.includes('待审批'));
    assert(requests.some((url) => url.includes('category=approval')));
    permitApproval = false;
    await page.goto(`${origin}/approvals`); await page.waitForLoadState('networkidle');
    assert.equal(await page.getByLabel('确认保留', { exact: true }).isEnabled(), false); permitApproval = true;
    await page.reload(); await page.getByLabel('确认保留', { exact: true }).click();
    assert.equal(submissions.length, 0); await page.getByRole('dialog').getByRole('button', { name: /返\s*回/ }).click(); assert.equal(submissions.length, 0);
    await page.getByLabel('确认保留', { exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: '确认保留', exact: true }).click();
    await page.waitForURL(`**/tasks/${taskId}`); assert.equal(submissions[0].decision, 'approve');
    await page.goto(`${origin}/approvals`); await page.waitForLoadState('networkidle');
    await page.getByText('补充说明 / 设置驳回原因与回修阶段', { exact: true }).click();
    await page.getByRole('combobox', { name: '驳回原因' }).click(); await page.getByText('问题验证不足', { exact: true }).click();
    await page.getByRole('button', { name: '打回并撤回', exact: true }).click(); await page.getByRole('dialog').getByRole('button', { name: '确认打回并重跑', exact: true }).click();
    await page.waitForURL(`**/tasks/${taskId}`); assert.equal(submissions[1].decision, 'reject'); assert.equal(submissions[1].retry_target, 'review_agent');
    assert.deepEqual(errors, []);
    const report = { result: 'passed', screenshots: process.env.UI_FLOWS_ONLY ? 0 : 24, themes: ['light', 'dark'], widths: [1440, 768, 390], assertions: ['no page overflow/errors', 'persistent verdict bar across tabs', 'archived verification rendered', 'hero tile opens its section', 'task sibling navigation/search', 'actual source diff', 'stale evidence cannot pass', 'task filter reaches API', 'server action permissions', 'approval cancellation causes no mutation', 'approval resumes matching task', 'rejection targets validation'], submissions: submissions.map((s) => ({ decision: s.decision, retry_target: s.retry_target })) };
    writeFileSync(join(artifacts, 'report.json'), JSON.stringify(report, null, 2));
    console.log(`PASS: ${process.env.UI_FLOWS_ONLY ? 0 : 24} responsive/theme cases and 12 integration assertions. ${artifacts}`);
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
