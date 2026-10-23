import { Button, Tag } from 'antd';
import type { StepRecord, TaskDetailResponse } from '../../lib/types';
import { formatDuration } from '../../lib/format';
import { StatusBadge } from '../../components/StatusBadge';
import { taskIsActive } from '../../lib/lifecycle';

type Dict = Record<string, unknown>;
const dict = (value: unknown): Dict => value && typeof value === 'object' && !Array.isArray(value) ? value as Dict : {};
const text = (value: unknown): string => typeof value === 'string' ? value.trim() : '';
const pick = (source: Dict, ...keys: string[]): string => {
  for (const key of keys) { const value = text(source[key]); if (value) return value; }
  return '';
};
const list = (value: unknown): string[] => Array.isArray(value) ? value.map(String).filter(Boolean).slice(0, 8) : [];
const latest = (steps: StepRecord[], name: string) => [...steps].reverse().find((step) => step.name === name);
const titles: Record<string, string> = {
  locate_agent: '根因定位', repair_agent: '代码修复', check_changes: '变更检查',
  verify_issue: '问题验证', run_regression: '回归验证', review_agent: '代码审查',
  prepare_delivery: '分支交付', request_approval: '最终审批', finalize_execution: '任务收尾',
};
function statusText(step?: StepRecord): string {
  if (!step) return '未开始';
  if (step.status === 'running') return '执行中';
  if (step.status === 'waiting') return '等待处理';
  if (step.status === 'failed' || ['FAIL', 'FAILED'].includes(String(step.result).toUpperCase())) return '失败';
  if (['MANUAL', 'INSUFFICIENT_EVIDENCE', 'UNVERIFIED'].includes(String(step.result).toUpperCase())) return '需关注';
  if (step.status === 'skipped') return '已跳过';
  if (step.status === 'succeeded') return '已完成';
  return '未开始';
}
function Stage({ title, steps, onOpen }: { title: string; steps: StepRecord[]; onOpen: (name: string) => void }) {
  const last = steps.at(-1);
  const failed = steps.some((step) => step.status === 'failed' || ['FAIL', 'FAILED'].includes(String(step.result).toUpperCase()));
  return <button type="button" className={`overview-stage ${failed ? 'is-danger' : last?.status === 'running' ? 'is-running' : last?.status === 'succeeded' ? 'is-success' : ''}`} onClick={() => last && onOpen(last.name)} disabled={!last}>
    <span className="overview-stage-mark">{failed ? '!' : last?.status === 'succeeded' ? '✓' : last?.status === 'running' ? '…' : '·'}</span>
    <span className="overview-stage-copy"><strong>{title}</strong><span>{steps.length ? statusText(last) : '未开始'}</span></span>
    {last && <span className="overview-stage-link">查看</span>}
  </button>;
}

export function TaskOverview({ detail, onOpenStep }: { detail: TaskDetailResponse; onOpenStep: (stepName: string) => void }) {
  const task = detail.task;
  const steps = task.steps ?? [];
  const report = dict(task.report);
  const problem = dict(task.problem);
  const lifecycle = dict(task.lifecycle);
  const active = taskIsActive(task);
  const result = String(task.result || pick(report, 'result', 'status') || 'UNKNOWN').toUpperCase();
  const locate = latest(steps, 'locate_agent');
  const repair = latest(steps, 'repair_agent');
  const check = latest(steps, 'check_changes');
  const verify = latest(steps, 'verify_issue');
  const regression = latest(steps, 'run_regression');
  const review = latest(steps, 'review_agent');
  const delivery = latest(steps, 'prepare_delivery');
  const approval = latest(steps, 'request_approval');
  const final = latest(steps, 'finalize_execution');
  const locateOut = dict(locate?.output);
  const repairOut = dict(repair?.output);
  const checkOut = dict(check?.output);
  const verifyOut = dict(verify?.output);
  const regressionOut = dict(regression?.output);
  const reviewOut = dict(review?.output);
  const deliveryOut = dict(delivery?.output);
  const resultReason = pick(lifecycle, 'terminal_reason') || pick(report, 'terminal_reason')
    || (active ? '' : [...steps].reverse().find((step) => step.status === 'failed')?.error?.message || '');
  const nextAction = pick(lifecycle, 'next_action') || pick(report, 'next_action');
  const rootCause = pick(locateOut, 'root_cause', 'analysis', 'summary') || locate?.summary || '';
  const claim = dict(checkOut.claim);
  const actualClaim = list(claim.actual);
  const changedFiles = Array.isArray(claim.actual) ? actualClaim : list(repairOut.changed_files);
  const actualClaimCount = Number(claim.actual_count ?? actualClaim.length) || 0;
  const changedFilesCount = Number(repairOut.changed_files_count ?? changedFiles.length) || 0;
  const displayChangedCount = claim.actual_count !== undefined ? actualClaimCount : changedFilesCount;
  const totals = dict(checkOut.totals);
  const branch = pick(deliveryOut, 'repair_branch');
  const commit = pick(deliveryOut, 'commit');
  const verificationStatus = verify ? statusText(verify) : '未开始';
  const regressionStatus = regression ? statusText(regression) : '未开始';
  const reviewStatus = pick(reviewOut, 'verdict') || statusText(review);
  const rawError = pick(problem, 'error', 'error_msg', 'raw_error', 'message');
  const duration = Number(report.elapsed_ms ?? task.duration_ms) || undefined;
  const stopStep = active ? pick(lifecycle, 'current_step') || task.current_step || '' : pick(lifecycle, 'terminal_step') || pick(report, 'terminal_step') || pick(final?.output ?? {}, 'terminal_step');
  const taskTitle = active ? '任务执行中' : task.approval_pending || approval?.status === 'waiting' || result === 'WAITING_APPROVAL'
    ? '等待最终审批' : ['FAILED', 'FAIL'].includes(result) || steps.some((step) => step.status === 'failed') ? '任务未完成'
      : ['MANUAL', 'INSUFFICIENT_EVIDENCE', 'BLOCKED'].includes(result) ? '需要人工处理' : result === 'REJECTED' ? '变更已拒绝' : '任务已结束';

  return <div className="task-overview task-overview-compact">
    <section className={`task-outcome-hero ${['FAILED', 'FAIL'].includes(result) ? 'is-danger' : ['MANUAL', 'INSUFFICIENT_EVIDENCE', 'BLOCKED'].includes(result) ? 'is-manual' : task.approval_pending ? 'is-waiting' : 'is-delivered'}`}>
      <div className="outcome-hero-main">
        <div className="outcome-eyebrow">任务结果</div>
        <div className="outcome-title-row"><h2>{taskTitle}</h2><StatusBadge kind="task" value={task.approval_pending ? 'WAITING_APPROVAL' : task.result} /></div>
        <p className="outcome-description">{active ? `当前节点：${titles[stopStep] || stopStep || '准备中'}` : pick(report, 'terminal_reason') || resultReason || '任务已停止，查看下方必要结果。'}</p>
        {resultReason && !active && <div className="outcome-reason"><span>停止原因</span><p>{resultReason}</p></div>}
        {nextAction && !active && <div className="outcome-next-action"><span>建议操作</span><strong>{nextAction}</strong></div>}
      </div>
      <div className="outcome-hero-facts">
        <div className="outcome-fact-row"><span>当前/停止节点</span><strong>{titles[stopStep] || stopStep || '—'}</strong></div>
        <div className="outcome-fact-row"><span>执行耗时</span><strong>{formatDuration(duration)}</strong></div>
        <div className="outcome-fact-row"><span>步骤</span><strong>{steps.filter((step) => step.status !== 'pending').length}/{steps.length}</strong></div>
        <div className="outcome-fact-row"><span>尝试次数</span><strong>{Number(task.attempt ?? lifecycle.attempt ?? 0) || 1}</strong></div>
      </div>
    </section>

    <section className="task-process-overview" aria-label="任务执行阶段">
      <div className="overview-section-heading"><div><h3>执行路径</h3><span>只显示阶段状态，点击阶段可查看对应节点</span></div></div>
      <div className="overview-stage-grid">
        <Stage title="根因定位" steps={[locate].filter(Boolean) as StepRecord[]} onOpen={onOpenStep} />
        <Stage title="代码修复" steps={[repair, check].filter(Boolean) as StepRecord[]} onOpen={onOpenStep} />
        <Stage title="验证与回归" steps={[verify, regression].filter(Boolean) as StepRecord[]} onOpen={onOpenStep} />
        <Stage title="代码审查" steps={[review].filter(Boolean) as StepRecord[]} onOpen={onOpenStep} />
        <Stage title="交付与审批" steps={[delivery, approval, final].filter(Boolean) as StepRecord[]} onOpen={onOpenStep} />
      </div>
    </section>

    <div className="task-outcome-grid task-outcome-grid-compact">
      <section className="overview-card">
        <header className="overview-card-head"><div><h3>问题与根因</h3></div>{pick(locateOut, 'category') && <Tag>{pick(locateOut, 'category')}</Tag>}</header>
        {rawError && <details className="overview-inline-details"><summary>查看原始异常</summary><pre>{rawError}</pre></details>}
        <p>{rootCause || (locate ? locate.summary || '尚未生成根因结论' : '根因定位尚未执行')}</p>
        {locate && <Button type="link" size="small" onClick={() => onOpenStep('locate_agent')}>查看定位节点</Button>}
      </section>
      <section className="overview-card">
        <header className="overview-card-head"><h3>验证状态</h3></header>
        <div className="overview-kv"><span>原问题验证</span><strong>{verificationStatus}</strong></div>
        <div className="overview-kv"><span>回归验证</span><strong>{regressionStatus}</strong></div>
        <div className="overview-kv"><span>代码审查</span><strong>{reviewStatus}</strong></div>
        {(pick(verifyOut, 'reason') || pick(regressionOut, 'reason') || pick(reviewOut, 'summary')) && <p>{pick(verifyOut, 'reason') || pick(regressionOut, 'reason') || pick(reviewOut, 'summary')}</p>}
        {verify && <Button type="link" size="small" onClick={() => onOpenStep('verify_issue')}>查看验证</Button>}
      </section>
      <section className="overview-card">
        <header className="overview-card-head"><h3>代码变更与交付</h3>{delivery?.status === 'succeeded' ? <Tag color="success">已交付</Tag> : <Tag>{statusText(delivery)}</Tag>}</header>
        <div className="overview-kv"><span>实际变更文件</span><strong>{displayChangedCount}</strong></div>
        {branch && <div className="overview-kv"><span>分支</span><strong className="is-mono" title={branch}>{branch}</strong></div>}
        {commit && <div className="overview-kv"><span>Commit</span><strong className="is-mono" title={commit}>{commit.slice(0, 12)}</strong></div>}
        {Object.keys(totals).length > 0 && <div className="overview-kv"><span>代码增删</span><strong>+{String(totals.added ?? 0)} / −{String(totals.removed ?? 0)}</strong></div>}
        {changedFiles.length > 0 && <details className="overview-inline-details"><summary>修改文件（{changedFiles.length}）</summary><ul>{changedFiles.map((file) => <li key={file}><code>{file}</code></li>)}</ul></details>}
        {delivery && <Button type="link" size="small" onClick={() => onOpenStep('prepare_delivery')}>查看交付节点</Button>}
      </section>
    </div>
  </div>;
}
