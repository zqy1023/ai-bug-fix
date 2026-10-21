import { useMemo, useState } from 'react';
import { App, Button, Drawer, Empty, Skeleton, Tag } from 'antd';
import {
  ArrowRightOutlined, BranchesOutlined, CheckCircleOutlined, ClockCircleOutlined,
  CodeOutlined, CopyOutlined, DiffOutlined, FileSearchOutlined, FileTextOutlined,
  GitlabOutlined, SafetyCertificateOutlined, StopOutlined, WarningOutlined,
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import type { StepRecord, TaskDetailResponse } from '../../lib/types';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { formatDuration } from '../../lib/format';
import { StatusBadge } from '../../components/StatusBadge';
import { CodeDiffViewer } from '../../components/CodeDiffViewer';

type Dict = Record<string, unknown>;
type Tone = 'success' | 'danger' | 'running' | 'waiting' | 'manual' | 'neutral' | 'pending' | 'skipped';

const STEP_LABELS: Record<string, string> = {
  discover_logs: '拉取异常日志',
  normalize_error: '规范化异常',
  deduplicate_error: '异常去重',
  identify_carrier: '识别承运商',
  prepare_source_snapshot: '源码快照',
  repair_branch_preflight: '检查已有分支',
  prepare_workspace: '准备隔离工作区',
  prepare_evidence: '准备定位证据',
  locate_agent: '根因定位',
  claim_repair_slot: '领取修复资格',
  repair_agent: '代码修复',
  check_changes: '变更门禁',
  verify_issue: '问题验证',
  run_regression: '回归验证',
  review_agent: 'AI 审查',
  prepare_delivery: '分支交付',
  request_approval: '最终审批',
  finalize_execution: '终态报告',
};

function dict(value: unknown): Dict {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Dict : {};
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function getText(source: Dict, ...keys: string[]): string {
  for (const key of keys) {
    const value = text(source[key]);
    if (value) return value;
  }
  return '';
}

function strings(value: unknown): string[] {
  const placeholders = new Set(['-', '—', '–', 'n/a', 'na', 'none', 'null', 'undefined']);
  return Array.isArray(value)
    ? value.map((item) => String(item ?? '').trim()).filter((item) => item && !placeholders.has(item.toLowerCase()))
    : [];
}

function rawString(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function shortText(value: string, maxChars = 220): string {
  const normalized = value.replace(/\s+/g, ' ').trim();
  if (normalized.length <= maxChars) return normalized;
  return `${normalized.slice(0, maxChars).trimEnd()}…`;
}

function objects(value: unknown): Dict[] {
  return Array.isArray(value) ? value.filter((item) => item !== null && typeof item === 'object' && !Array.isArray(item)).map((item) => item as Dict) : [];
}

function stepOf(steps: StepRecord[], name: string): StepRecord | undefined {
  return [...steps].reverse().find((step) => step.name === name);
}

function toneForStep(step?: StepRecord): Tone {
  if (!step) return 'pending';
  const result = String(step.result ?? '').toUpperCase();
  if (step.status === 'failed' || ['FAIL', 'FAILED', 'REJECTED'].includes(result)) return 'danger';
  if (step.status === 'running') return 'running';
  if (step.status === 'waiting') return 'waiting';
  if (['MANUAL', 'INSUFFICIENT_EVIDENCE'].includes(result)) return 'manual';
  if (step.status === 'skipped') return 'skipped';
  if (step.status === 'succeeded') return 'success';
  return 'pending';
}

function toneText(tone: Tone): string {
  return ({
    success: '已完成',
    danger: '未通过',
    running: '执行中',
    waiting: '等待处理',
    manual: '需人工处理',
    skipped: '已跳过',
    neutral: '未执行',
    pending: '未开始',
  })[tone];
}

function stepSummary(step?: StepRecord): string {
  if (!step) return '';
  const output = dict(step.output);
  return getText(output, 'summary', 'reason', 'root_cause', 'terminal_reason') || text(step.summary) || text(step.error?.message);
}

function terminalLabel(step: string): string {
  return STEP_LABELS[step] || step || '终态尚未记录';
}

function fileStatusLabel(value: unknown): string {
  const status = text(value).toLowerCase();
  if (status === 'declared') return '待核验';
  if (['added', 'new', 'created'].includes(status)) return '新增';
  if (['deleted', 'removed'].includes(status)) return '删除';
  if (['renamed', 'moved'].includes(status)) return '重命名';
  return '修改';
}

function resultHeadline(result: string, analysisOnly: boolean, deliveredCode: boolean, delivered: boolean, changedCount: number, changeEvidenceKnown: boolean, finalApprovalPending: boolean, executionFailed: boolean): string {
  if (executionFailed || ['FAILED', 'FAIL'].includes(result)) return '任务未完成';
  if (finalApprovalPending) return '源码分支已提交，等待最终审批';
  if (result === 'CANCELLED') return '任务已取消';
  if (result === 'REJECTED') return '变更已被拒绝';
  if (analysisOnly && delivered) return '分析报告已提交';
  if (deliveredCode) return '源码修改已提交';
  if (analysisOnly && changedCount > 0 && changeEvidenceKnown) return '源码有改动，但本次仅交付分析报告';
  if (analysisOnly && changedCount > 0) return '仅交付分析报告，源码声明尚未核验';
  if (analysisOnly) return '本次走分析报告交付路径';
  if (changedCount > 0 && changeEvidenceKnown) return '检测到代码改动，但尚未完成源码交付';
  if (changedCount > 0) return '修复 Agent 声明了改动，尚未完成差异核验';
  if (['MANUAL', 'INSUFFICIENT_EVIDENCE'].includes(result)) return '分析结束，需人工处理';
  if (['RUNNING', 'PENDING', 'QUEUED', 'WAITING_APPROVAL'].includes(result)) return '任务仍在处理中';
  if (delivered) return '交付记录已生成';
  return result ? '任务已结束' : '任务结果待确认';
}

function resultDescription(analysisOnly: boolean, delivered: boolean, deliveredCode: boolean, changedCount: number, changeEvidenceKnown: boolean, finalApprovalPending: boolean, result: string, executionFailed: boolean): string {
  if (executionFailed || result === 'FAILED' || result === 'FAIL') return '任务未完成；请查看停止原因和失败节点。';
  if (finalApprovalPending) return '源码分支已提交，等待最终审批；系统不会自动合并。';
  if (analysisOnly && delivered) return '仅提交 final.json 分析报告，源码改动未随分支交付。';
  if (analysisOnly && changedCount > 0 && changeEvidenceKnown) return `检测到 ${changedCount} 个源码文件有改动，但本次只交付分析报告。`;
  if (analysisOnly && changedCount > 0) return `检测到 ${changedCount} 个修改声明，尚未核实真实差异；本次只交付分析报告。`;
  if (analysisOnly) return '本次仅交付分析报告，未提交源码修复。';
  if (deliveredCode) return `已交付 ${changedCount} 个源码变更文件，等待你检查并手动合并。`;
  if (changedCount > 0 && !changeEvidenceKnown) return '修改文件尚未完成基线比对，不能确认实际差异。';
  if (changedCount > 0) return '已发现源码差异，但尚无可靠的分支交付证据。';
  if (changeEvidenceKnown) return '基线比对未发现源码差异。';
  if (result === 'MANUAL' || result === 'INSUFFICIENT_EVIDENCE') return '证据不足或超出自动修复范围，需人工处理。';
  if (['RUNNING', 'PENDING', 'QUEUED', 'WAITING_APPROVAL'].includes(result)) return '任务仍在执行或等待人工决策。';
  if (delivered) return '交付记录已生成。';
  return result ? '任务已结束；详情见下方各阶段结果。' : '任务结果待确认。';
}
function StageItem({ title, steps, hint }: { title: string; steps: Array<StepRecord | undefined>; hint?: string }) {
  const completed = steps.filter(Boolean) as StepRecord[];
  let tone: Tone = 'pending';
  if (completed.some((step) => toneForStep(step) === 'danger')) tone = 'danger';
  else if (completed.some((step) => toneForStep(step) === 'running')) tone = 'running';
  else if (completed.some((step) => toneForStep(step) === 'waiting')) tone = 'waiting';
  else if (completed.some((step) => toneForStep(step) === 'manual')) tone = 'manual';
  else if (completed.every((step) => toneForStep(step) === 'skipped')) tone = 'skipped';
  else if (completed.length) tone = 'success';

  return (
    <div className={`overview-stage is-${tone}`}>
      <span className="overview-stage-mark">
        {tone === 'success' ? <CheckCircleOutlined />
          : tone === 'danger' ? <StopOutlined />
            : tone === 'running' ? <ClockCircleOutlined />
              : tone === 'manual' ? <FileTextOutlined />
                : tone === 'waiting' ? <ClockCircleOutlined />
                  : tone === 'skipped' ? <StopOutlined />
                    : <span className="overview-stage-empty" />}
      </span>
      <div className="overview-stage-copy">
        <strong>{title}</strong>
        <span>{hint || toneText(tone)}</span>
      </div>
    </div>
  );
}

function KeyValue({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  if (!value) return null;
  return (
    <div className="overview-kv">
      <span>{label}</span>
      <strong className={mono ? 'is-mono' : ''} title={value}>{value}</strong>
    </div>
  );
}

export function TaskOverview({
  detail, onOpenStep,
}: {
  detail: TaskDetailResponse;
  onOpenStep: (stepName: string) => void;
}) {
  const { message } = App.useApp();
  const task = detail.task;
  const steps = task.steps ?? [];
  const problem = dict(task.problem);
  const originalError = rawString(problem.error).trim() || rawString(problem.raw_error).trim();
  const report = dict(task.report);
  const result = String(task.result || getText(report, 'result', 'status') || '').toUpperCase();
  const lifecycle = dict(task.lifecycle);
  const lifecycleStatus = getText(lifecycle, 'status').toLowerCase();
  const taskActive = ['queued', 'running', 'retrying'].includes(lifecycleStatus)
    || ['RUNNING', 'RETRYING', 'PENDING'].includes(result);
  const activeStep = getText(lifecycle, 'current_step') || String(task.current_step || '');
  const taskAttempt = Number(task.attempt ?? lifecycle.attempt ?? 0) || 0;
  const runError = dict(detail.run_error);
  const recoveryFailed = task.approval_pending === true
    && detail.run_status === 'failed'
    && getText(runError, 'status').toUpperCase() === 'FAILED';

  const locateStep = stepOf(steps, 'locate_agent');
  const repairStep = stepOf(steps, 'repair_agent');
  const checkStep = stepOf(steps, 'check_changes');
  const verifyStep = stepOf(steps, 'verify_issue');
  const regressionStep = stepOf(steps, 'run_regression');
  const reviewStep = stepOf(steps, 'review_agent');
  const deliveryStep = stepOf(steps, 'prepare_delivery');
  const approvalStep = stepOf(steps, 'request_approval');
  const finalStep = stepOf(steps, 'finalize_execution');

  const locate = dict(locateStep?.output);
  const repair = dict(repairStep?.output);
  const gate = dict(checkStep?.output);
  const verify = dict(verifyStep?.output);
  const regression = dict(regressionStep?.output);
  const review = dict(reviewStep?.output);
  const delivery = dict(deliveryStep?.output);
  const deliveryInput = dict(deliveryStep?.input);

  const claimedFilePaths = strings(repair.changed_files);
  const gateFiles = objects(gate.files).filter((file) => text(file.path) && text(file.status) !== 'truncated');
  const fileByPath = new Map<string, Dict>(gateFiles.map((file) => [text(file.path), file] as const));
  const claim = dict(gate.claim);
  const gateScope = dict(gate.scope);
  // A step record alone is not proof that diffing completed: blocked checks can emit an
  // output-shaped object without the actual-vs-baseline claim. Require that evidence.
  const hasActualClaim = Array.isArray(claim.actual);
  const changeEvidenceKnown = Boolean(
    checkStep && hasActualClaim
    && (gateScope.diffable === true || Number(gateScope.baseline_files || 0) > 0),
  );
  const actualFilePaths = strings(claim.actual);
  const actualFromFileEvidence = gateFiles
    .filter((file) => ['added', 'modified', 'deleted', 'changed', 'modified_too_large'].includes(text(file.status).toLowerCase()))
    .map((file) => text(file.path));
  const verifiedFilePaths = actualFilePaths.length ? actualFilePaths : actualFromFileEvidence;
  const displayFilePaths = changeEvidenceKnown ? verifiedFilePaths : claimedFilePaths;
  const changedFiles = displayFilePaths.map((path) => fileByPath.get(path) || {
    path,
    status: changeEvidenceKnown ? 'modified' : 'declared',
    added: 0,
    removed: 0,
  });
  const changedCount = changedFiles.length;
  const gateTotals = dict(gate.totals);
  const added = Number(gateTotals.added ?? changedFiles.reduce((sum, file) => sum + Number(file.added || 0), 0)) || 0;
  const removed = Number(gateTotals.removed ?? changedFiles.reduce((sum, file) => sum + Number(file.removed || 0), 0)) || 0;

  const branch = getText(delivery, 'repair_branch') || getText(deliveryStep?.input ?? {}, 'repair_branch');
  const commit = getText(delivery, 'commit');
  const pushed = delivery.pushed === true;
  const localBranch = delivery.local_branch === true;
  const landed = Boolean(commit && (pushed || localBranch) && deliveryStep?.status === 'succeeded');
  const analysisOnly = deliveryInput.analysis_only === true;
  const finalApprovalPending = Boolean(
    !recoveryFailed && !analysisOnly && landed && (
      task.approval_pending === true
      || approvalStep?.status === 'waiting'
      || result === 'WAITING_APPROVAL'
    ),
  );
  const deliveredCode = landed && !analysisOnly;
  const deliveredReport = landed && analysisOnly;
  const reportPaths = strings(deliveryInput.report_paths);
  const terminalStep = taskActive
    ? activeStep
    : getText(lifecycle, 'terminal_step') || getText(report, 'terminal_step') || getText(finalStep?.output ?? {}, 'terminal_step') || '';
  const lastExecutedStep = [...steps]
    .filter((step) => step.status !== 'pending')
    .sort((left, right) => {
      const leftTime = Date.parse(left.finished_at || left.started_at || '') || 0;
      const rightTime = Date.parse(right.finished_at || right.started_at || '') || 0;
      return leftTime - rightTime;
    })
    .slice(-1)[0];
  const terminalReason = taskActive ? ''
    : (recoveryFailed
      ? getText(runError, 'terminal_reason', 'message')
      : '') || getText(lifecycle, 'terminal_reason') || getText(report, 'terminal_reason') || getText(finalStep?.output ?? {}, 'terminal_reason') || '';
  const nextAction = taskActive ? ''
    : (recoveryFailed
      ? getText(runError, 'next_action')
      : '') || getText(lifecycle, 'next_action') || getText(report, 'next_action') || getText(finalStep?.output ?? {}, 'next_action') || '';
  const rootCause = getText(locate, 'root_cause', 'analysis');
  const analysisSummary = getText(locate, 'summary') || stepSummary(locateStep) || getText(report, 'root_cause');
  const category = getText(locate, 'category');
  const scope = getText(locate, 'recommended_scope');
  const evidence = objects(locate.source_evidence);
  const facts = strings(locate.facts);
  const evidenceGaps = strings(locate.evidence_gaps);
  const reviewVerdict = getText(review, 'verdict', 'result') || String(reviewStep?.result || '').toUpperCase();
  const reviewSummary = getText(review, 'summary') || stepSummary(reviewStep);
  const findings = strings(review.findings);
  const requiredChanges = strings(review.required_changes);
  const verifyMode = getText(verify, 'mode');
  const syntaxOnlyVerification = verifyMode === 'syntax_only';
  const verifyPassed = verify.passed === true && !syntaxOnlyVerification;
  const regressionPassed = regression.passed === true;
  const qualitySteps = [verifyStep, regressionStep].filter(Boolean) as StepRecord[];
  const qualityFailed = qualitySteps.some((step) => toneForStep(step) === 'danger');
  const resultFailed = !taskActive && (recoveryFailed || ['FAILED', 'FAIL'].includes(result) || qualityFailed || deliveryStep?.status === 'failed');
  const terminalReasonFromFailure = recoveryFailed
    ? terminalReason || '驳回请求已受理，但后台恢复执行失败。'
    : terminalReason || (
      [...steps].reverse().find((step) => step.status === 'failed')?.error?.message || ''
    );
  const originalErrorPreview = originalError.split(/\r?\n/)[0] || originalError;

  const diffArtifact = useMemo(() => (
    checkStep?.artifacts?.find((artifact) => artifact.name === 'diff' || artifact.path.endsWith('/diff.json'))
  ), [checkStep?.artifacts]);
  const [diffOpen, setDiffOpen] = useState(false);
  const [selectedDiffPath, setSelectedDiffPath] = useState('');
  const diffQuery = useQuery({
    queryKey: queryKeys.artifactContent(detail.run_id, diffArtifact?.path || '', task.task_id),
    queryFn: () => api.artifactContent(detail.run_id, diffArtifact!.path, task.task_id),
    enabled: diffOpen && Boolean(diffArtifact),
    staleTime: 60_000,
  });

  const resultText = resultHeadline(result, analysisOnly, deliveredCode, landed, changedCount, changeEvidenceKnown, finalApprovalPending, resultFailed);
  const description = resultDescription(analysisOnly, landed, deliveredCode, changedCount, changeEvidenceKnown, finalApprovalPending, result, resultFailed);
  const diffPayload = diffQuery.data?.content;

  const copyCommit = async () => {
    if (!commit) return;
    try {
      await navigator.clipboard.writeText(commit);
      message.success('Commit SHA 已复制');
    } catch {
      message.warning('复制失败，请手动选择 Commit SHA');
    }
  };

  const copyBranch = async () => {
    if (!branch) return;
    try {
      await navigator.clipboard.writeText(branch);
      message.success('分支名已复制');
    } catch {
      message.warning('复制失败，请手动选择分支名');
    }
  };

  return (
    <div className="task-overview">
      <section className={`task-outcome-hero ${resultFailed ? 'is-danger' : deliveredCode || deliveredReport || finalApprovalPending ? 'is-delivered' : ['MANUAL', 'INSUFFICIENT_EVIDENCE'].includes(result) ? 'is-manual' : ''}`}>
        <div className="outcome-hero-main">
          <div className="outcome-eyebrow">任务结果</div>
          <div className="outcome-title-row">
            <h2>{taskActive
              ? lifecycleStatus === 'retrying' || result === 'RETRYING' ? '任务重试中' : '任务执行中'
              : recoveryFailed ? '驳回后的恢复失败' : resultText}</h2>
            <StatusBadge kind="task" value={taskActive ? lifecycleStatus === 'retrying' || result === 'RETRYING' ? 'RETRYING' : 'RUNNING' : recoveryFailed ? 'FAILED' : finalApprovalPending ? 'WAITING_APPROVAL' : task.result} />
          </div>
          <p className="outcome-description">
            {taskActive
              ? `当前节点：${terminalLabel(activeStep)}${taskAttempt > 0 ? ` · 第 ${taskAttempt} 次尝试` : ''}。较早尝试的错误不会覆盖当前执行状态。`
              : recoveryFailed
                ? '审批请求已受理，但后台没有完成恢复执行。请核对失败原因；待审批记录仍保留，可修复环境后重新提交决策。'
                : description}
          </p>
          {terminalReasonFromFailure && (
            <div className={`outcome-reason ${resultFailed ? 'is-danger' : ''}`}>
              <span>{resultFailed ? '停止原因' : '结论说明'}</span>
              <div className="outcome-reason-copy">
                <p>{shortText(terminalReasonFromFailure, 220)}</p>
                {terminalReasonFromFailure.length > 220 && (
                  <details className="overview-inline-details">
                    <summary>查看完整原因</summary>
                    <p>{terminalReasonFromFailure}</p>
                  </details>
                )}
              </div>
            </div>
          )}
          {nextAction && (
            <div className="outcome-next-action">
              <ArrowRightOutlined />
              <div>
                <span>下一步</span>
                <strong>{shortText(nextAction, 150)}</strong>
                {nextAction.length > 150 && (
                  <details className="overview-inline-details">
                    <summary>查看完整建议</summary>
                    <p>{nextAction}</p>
                  </details>
                )}
              </div>
            </div>
          )}
        </div>
        <div className="outcome-hero-facts">
          <div className="outcome-fact-primary">
            <span>源码改动</span>
            <strong>{changedCount > 0
              ? changeEvidenceKnown ? `${changedCount} 个实际变更文件` : `${changedCount} 个文件声明待核验`
              : changeEvidenceKnown ? '已核验：没有真实差异' : '尚无可核验源码差异'}</strong>
            <small>{changedCount > 0 && changeEvidenceKnown ? `+${added} / −${removed} 行` : changeEvidenceKnown ? '基线差异核验已完成' : '未完成确定性基线比对'}</small>
          </div>
          <div className="outcome-fact-row">
            <span>{taskActive ? '当前执行节点' : '自动修复停止点'}</span>
            <strong>{terminalLabel(terminalStep)}</strong>
          </div>
          <div className="outcome-fact-row">
            <span>最后执行步骤</span>
            <strong>{terminalLabel(lastExecutedStep?.name || '')}</strong>
          </div>
          <div className="outcome-fact-row">
            <span>执行耗时</span>
            <strong>{formatDuration(Number(report.elapsed_ms ?? task.duration_ms) || undefined)}</strong>
          </div>
          <div className="outcome-fact-row">
            <span>已记录步骤</span>
            <strong>{steps.length} 个</strong>
          </div>
          {taskAttempt > 0 && (
            <div className="outcome-fact-row">
              <span>任务尝试</span>
              <strong>第 {taskAttempt} 次</strong>
            </div>
          )}
        </div>
      </section>

      <section className="task-process-overview" aria-label="任务执行阶段">
        <div className="overview-section-heading">
          <div><h3>处理路径</h3><span>只展示业务阶段；完整原子节点与原始数据位于下方执行记录</span></div>
          {terminalStep && <span className="overview-stop-point">自动修复停止点：{terminalLabel(terminalStep)}</span>}
        </div>
        <div className="overview-stage-grid">
          <StageItem title="根因定位" steps={[locateStep]} hint={locateStep ? toneText(toneForStep(locateStep)) : '未开始'} />
          <StageItem title="代码修改" steps={[repairStep, checkStep]} hint={analysisOnly ? '未随报告分支交付源码' : changedCount ? (changeEvidenceKnown ? `${changedCount} 个实际变更文件` : `${changedCount} 个文件声明待核验`) : changeEvidenceKnown ? '基线比对未发现差异' : repairStep ? toneText(toneForStep(repairStep)) : '未开始'} />
          <StageItem title="验证与回归" steps={[verifyStep, regressionStep]} hint={qualitySteps.length ? qualityFailed ? '验证未通过' : '验证节点已执行' : '未执行'} />
          <StageItem title="AI 审查" steps={[reviewStep]} hint={reviewVerdict ? ({
            PASS: '审查通过，进入分支交付',
            FAIL: '审查未通过，触发回修',
            MANUAL: analysisOnly
              ? '仅交付报告，不进入源码审批'
              : finalApprovalPending
                ? '源码分支已提交，等待最终审批'
                : landed
                  ? '源码分支已提交，待最终审批'
                  : '先提交候选分支，再人工复核',
          } as Record<string, string>)[reviewVerdict] || reviewVerdict : '未执行'} />
          <StageItem title="Git 交付" steps={[deliveryStep]} hint={landed ? analysisOnly ? '分析报告分支已提交' : '修复分支已提交' : deliveryStep ? toneText(toneForStep(deliveryStep)) : '未交付'} />
        </div>
      </section>

      <div className="task-outcome-grid">
        <section className="overview-card analysis-card">
          <header className="overview-card-head">
            <div className="overview-card-icon"><FileSearchOutlined /></div>
            <div><h3>根因分析</h3><span>先看结论，证据按需展开</span></div>
            {category && !['carrier', 'unknown', 'none', 'null', '-'].includes(category.toLowerCase()) && <Tag>{category}</Tag>}
          </header>
          {originalError && (
            <details className="overview-inline-details task-original-error-details">
              <summary>
                <WarningOutlined /> 原始 Error
                <span className="task-original-error-preview">{originalErrorPreview}</span>
                <span className="task-original-error-expand">查看全文</span>
              </summary>
              <pre className="task-original-error-text">{originalError}</pre>
            </details>
          )}
          {rootCause || analysisSummary || facts.length > 0 || evidence.length > 0 || evidenceGaps.length > 0 ? (
            <div className="analysis-result">
              <div className="analysis-root-cause">
                <label>根因摘要</label>
                <p className="analysis-root-cause-summary">{shortText(rootCause || analysisSummary || '已完成定位，尚未生成简短根因摘要。')}</p>
              </div>
              {(rootCause.length > 220 || (analysisSummary && analysisSummary !== rootCause) || facts.length > 0 || evidence.length > 0 || evidenceGaps.length > 0) && (
                <details className="overview-inline-details analysis-evidence-details">
                  <summary>查看完整分析与证据（{facts.length + evidence.length + evidenceGaps.length + (rootCause.length > 220 ? 1 : 0)} 项）</summary>
                  {rootCause.length > 220 && (
                    <div className="analysis-detail-block">
                      <label>完整根因</label>
                      <p>{rootCause}</p>
                    </div>
                  )}
                  {analysisSummary && analysisSummary !== rootCause && (
                    <div className="analysis-detail-block">
                      <label>定位摘要</label>
                      <p>{analysisSummary}</p>
                    </div>
                  )}
                  {facts.length > 0 && (
                    <div className="analysis-detail-block">
                      <label>定位依据</label>
                      <ul>{facts.map((fact, index) => <li key={`${index}-${fact}`}>{fact}</li>)}</ul>
                    </div>
                  )}
                  {evidence.length > 0 && (
                    <div className="analysis-detail-block">
                      <label>源码证据（{evidence.length}）</label>
                      {evidence.map((item, index) => (
                        <div className="source-evidence-row" key={`${text(item.path)}-${index}`}>
                          <code>{text(item.path) || '源码位置未记录'}{text(item.symbol) ? ` · ${text(item.symbol)}` : ''}</code>
                          {text(item.finding) && <p>{text(item.finding)}</p>}
                        </div>
                      ))}
                    </div>
                  )}
                  {scope && <div className="analysis-meta"><span><b>建议范围</b>{scope.toLowerCase() === 'carrier' ? '单一承运商' : scope.toLowerCase() === 'project' ? '项目 / 共享层' : scope}</span></div>}
                  {evidenceGaps.length > 0 && (
                    <div className="analysis-detail-block">
                      <label>证据缺口（{evidenceGaps.length}）</label>
                      <ul>{evidenceGaps.map((item) => <li key={item}>{item}</li>)}</ul>
                    </div>
                  )}
                </details>
              )}
            </div>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="定位步骤尚未产出根因结论" />
          )}
          {locateStep && <button type="button" className="overview-text-link" onClick={() => onOpenStep('locate_agent')}>查看定位步骤 <ArrowRightOutlined /></button>}
        </section>

        <section className="overview-card changes-card">
          <header className="overview-card-head">
            <div className="overview-card-icon"><CodeOutlined /></div>
            <div><h3>代码修改</h3><span>区分工作区改动与真正提交到分支的源码</span></div>
            <Tag color={deliveredCode ? 'success' : changedCount && changeEvidenceKnown ? 'gold' : !changeEvidenceKnown && claimedFilePaths.length ? 'gold' : 'default'}>
              {deliveredCode ? '源码已提交' : analysisOnly ? '仅交付报告' : changedCount && changeEvidenceKnown ? '实际改动未交付' : !changeEvidenceKnown && claimedFilePaths.length ? '改动声明待核验' : changeEvidenceKnown ? '无实际差异' : '暂无改动记录'}
            </Tag>
          </header>
          {analysisOnly && (
            <div className="overview-note is-warning">
              本次仅提交分析报告；源码改动未随该分支交付。
            </div>
          )}
          {repairStep && (getText(repair, 'summary', 'analysis') || stepSummary(repairStep)) && (
            <details className="overview-inline-details">
              <summary>查看修复说明</summary>
              <p className="change-summary">{getText(repair, 'summary', 'analysis') || stepSummary(repairStep)}</p>
            </details>
          )}
          {changedFiles.length > 0 ? (
            <>
              <div className="change-count-line">
                <strong>{changeEvidenceKnown ? `${changedCount} 个真实变更文件` : `${changedCount} 个声明文件（待核验）`}</strong>
                {changeEvidenceKnown && <span className="diff-add">+{added}</span>}
                {changeEvidenceKnown && <span className="diff-remove">−{removed}</span>}
              </div>
              <ul className="changed-file-list">
                {changedFiles.slice(0, 3).map((file, index) => (
                  <li key={`${text(file.path)}-${index}`}>
                    <span className={`file-change-mark is-${String(file.status || 'modified').toLowerCase()}`} />
                    <span className="file-change-status">{fileStatusLabel(file.status)}</span>
                    <button
                      type="button"
                      className="changed-file-path-button"
                      title={text(file.path)}
                      disabled={!diffArtifact}
                      onClick={() => {
                        setSelectedDiffPath(text(file.path));
                        setDiffOpen(true);
                      }}
                    >
                      <code>{text(file.path)}</code>
                    </button>
                    <span className="file-change-lines">
                      {Number(file.added || 0) > 0 && <b className="diff-add">+{Number(file.added)}</b>}
                      {Number(file.removed || 0) > 0 && <b className="diff-remove">−{Number(file.removed)}</b>}
                    </span>
                  </li>
                ))}
              </ul>
              {changedCount > 3 && (
                <details className="overview-inline-details">
                  <summary>其余 {changedCount - 3} 个文件</summary>
                  <ul className="changed-file-list">
                    {changedFiles.slice(3).map((file, index) => (
                      <li key={`${text(file.path)}-more-${index}`}>
                        <span className={`file-change-mark is-${String(file.status || 'modified').toLowerCase()}`} />
                        <span className="file-change-status">{fileStatusLabel(file.status)}</span>
                        <button
                      type="button"
                      className="changed-file-path-button"
                      title={text(file.path)}
                      disabled={!diffArtifact}
                      onClick={() => {
                        setSelectedDiffPath(text(file.path));
                        setDiffOpen(true);
                      }}
                    >
                      <code>{text(file.path)}</code>
                    </button>
                        <span className="file-change-lines">
                          {Number(file.added || 0) > 0 && <b className="diff-add">+{Number(file.added)}</b>}
                          {Number(file.removed || 0) > 0 && <b className="diff-remove">−{Number(file.removed)}</b>}
                        </span>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          ) : (
            <div className="overview-no-change">
              <strong>{changeEvidenceKnown ? '没有检测到真实源码差异' : claimedFilePaths.length ? '修改声明尚未核验' : '没有可核验的源码差异'}</strong>
              <span>{changeEvidenceKnown ? '确定性门禁已比对基线；未将模型声明当作实际修改。' : claimedFilePaths.length ? '修复 Agent 声明了候选文件，但还没有确定性差异证据。' : '任务可能只做根因分析，或在修改前已终止。'}</span>
            </div>
          )}
          <div className="overview-card-actions">
            {diffArtifact && (
              <Button size="small" icon={<DiffOutlined />} onClick={() => setDiffOpen(true)}>查看代码差异</Button>
            )}
            {checkStep && <Button size="small" type="link" onClick={() => onOpenStep('check_changes')}>变更门禁详情</Button>}
          </div>
        </section>

        <section className="overview-card quality-card">
          <header className="overview-card-head">
            <div className="overview-card-icon"><SafetyCertificateOutlined /></div>
            <div><h3>验证与审查</h3><span>{analysisOnly
              ? '当前分支交付的是分析报告，不进入源码修复审批'
              : 'AI 审查不是人工审批；最终人工审批只在源码分支成功提交后出现'}</span></div>
            {reviewVerdict && <Tag color={reviewVerdict === 'PASS' ? 'success' : reviewVerdict === 'FAIL' ? 'error' : 'gold'}>{({
              PASS: 'AI 审查通过',
              FAIL: 'AI 审查未通过',
              MANUAL: analysisOnly ? '报告路径' : finalApprovalPending ? '等待最终审批' : landed ? '已交付，待审批' : '先交付再审批',
            } as Record<string, string>)[reviewVerdict] || reviewVerdict}</Tag>}
          </header>
          <div className="quality-check-list">
            <div className="quality-check-row">
              <span>问题验证</span>
              <strong className={syntaxOnlyVerification ? 'is-muted' : verifyPassed ? 'is-good' : verifyStep ? 'is-bad' : 'is-muted'}>
                {syntaxOnlyVerification ? '仅语法检查' : verifyPassed ? '通过' : verifyStep ? '未通过' : '未执行'}
              </strong>
              <small className="quality-detail-clamp">
                {syntaxOnlyVerification
                  ? '未执行原始问题重放'
                  : getText(verify, 'mode')}{getText(verify, 'reason') && !syntaxOnlyVerification ? ` · ${getText(verify, 'reason')}` : ''}
              </small>
            </div>
            <div className="quality-check-row">
              <span>回归测试</span>
              <strong className={regressionPassed ? 'is-good' : regressionStep ? 'is-bad' : 'is-muted'}>
                {regressionPassed ? '通过' : regressionStep ? '未通过' : '未执行'}
              </strong>
              <small className="quality-detail-clamp">
                {regression.command_count !== undefined ? `${String(regression.command_count)} 条命令` : '未记录命令数'}
                {getText(regression, 'reason') ? ` · ${getText(regression, 'reason')}` : ''}
              </small>
            </div>
          </div>
          <div className="review-result-block">
            <label>{analysisOnly ? 'AI 审查结论（报告路径）' : 'AI 审查结论（非最终审批）'}</label>
            {reviewStep ? (
              <>
                <p className="review-summary-compact">{shortText(reviewSummary || '审查步骤已执行，但没有记录摘要。', 220)}</p>
                {(reviewSummary.length > 180 || findings.length > 0 || requiredChanges.length > 0) && (
                  <details className="overview-inline-details review-full-details">
                    <summary>展开完整审查内容{findings.length ? ` · ${findings.length} 项发现` : ''}</summary>
                    <p className="review-summary-full">{reviewSummary || '未记录审查摘要。'}</p>
                    {findings.length > 0 && (
                      <div className="analysis-detail-block">
                        <label>审查发现</label>
                        <ul className="review-findings">
                          {findings.map((finding) => <li key={finding}>{finding}</li>)}
                        </ul>
                      </div>
                    )}
                    {requiredChanges.length > 0 && (
                      <div className="analysis-detail-block">
                        <label>需要处理的事项（{requiredChanges.length}）</label>
                        <ul>{requiredChanges.map((item) => <li key={item}>{item}</li>)}</ul>
                      </div>
                    )}
                  </details>
                )}
              </>
            ) : (
              <p className="overview-muted-line">未产生 AI 审查结论。任务可能在修复、验证阶段前结束，或处于人工审查模式。</p>
            )}
          </div>
          <div className="overview-card-actions">
            {verifyStep && <Button size="small" type="link" onClick={() => onOpenStep('verify_issue')}>问题验证详情</Button>}
            {regressionStep && <Button size="small" type="link" onClick={() => onOpenStep('run_regression')}>回归详情</Button>}
            {reviewStep && <Button size="small" type="link" onClick={() => onOpenStep('review_agent')}>完整审查记录</Button>}
          </div>
        </section>

        <section className="overview-card delivery-card">
          <header className="overview-card-head">
            <div className="overview-card-icon"><BranchesOutlined /></div>
            <div><h3>Git 分支交付</h3><span>分支里有什么、提交到哪里、能否核验</span></div>
            <Tag color={landed ? 'success' : deliveryStep?.status === 'failed' ? 'error' : 'default'}>
              {landed ? '提交已落地' : deliveryStep?.status === 'failed' ? '交付失败' : '未确认提交'}
            </Tag>
          </header>
          {deliveryStep ? (
            <>
              <div className="delivery-destination">
                <span className="delivery-destination-icon"><GitlabOutlined /></span>
                <div>
                  <label>{analysisOnly ? '分析报告分支' : '修复分支'}</label>
                  <code>{branch || '分支名未记录'}</code>
                </div>
              </div>
              <div className="delivery-kv-list">
                <KeyValue label="Commit SHA" value={commit} mono />
                <KeyValue label="交付位置" value={pushed ? '已推送远端' : localBranch ? '仅写入本地仓库' : '未确认推送或落地'} />
                <KeyValue label="提交内容" value={analysisOnly ? (reportPaths.join('、') || 'final.json 分析报告') : changedCount ? `${changedCount} 个源码文件` : '未记录源码文件清单'} />
              </div>
              {(branch || commit) && (
                <div className="overview-card-actions">
                  {branch && <Button size="small" icon={<CopyOutlined />} onClick={() => void copyBranch()}>复制分支名</Button>}
                  {commit && <Button size="small" icon={<CopyOutlined />} onClick={() => void copyCommit()}>复制 Commit SHA</Button>}
                  {deliveryStep && <Button size="small" type="link" onClick={() => onOpenStep('prepare_delivery')}>交付步骤记录</Button>}
                </div>
              )}
              <div className={`delivery-footnote ${landed ? 'is-good' : 'is-warning'}`}>
                {landed
                  ? '系统没有自动合并。请在 Git 上核对变更并由你手动合并。'
                  : deliveryStep.status === 'failed'
                    ? stepSummary(deliveryStep) || '交付步骤失败，请查看节点输入、错误和日志。'
                    : '没有足够的提交证据证明分支已可靠落地。'}
              </div>
            </>
          ) : (
            <div className="overview-no-change">
              <strong>尚未进入分支交付</strong>
              <span>当前任务没有可核验的修复分支或报告分支提交。</span>
            </div>
          )}
        </section>
      </div>

      <Drawer
        title={`代码差异 · ${text(diffArtifact?.path) || 'diff.json'}`}
        placement="right"
        width="min(1400px, 96vw)"
        open={diffOpen}
        onClose={() => setDiffOpen(false)}
        styles={{ body: { padding: 10, overflow: 'hidden' } }}
      >
        {diffQuery.isLoading ? <Skeleton active paragraph={{ rows: 8 }} />
          : diffQuery.isError ? <Empty description="差异产物读取失败，请从 check_changes 步骤核对错误。" />
            : diffPayload ? (
              <CodeDiffViewer
                payload={diffPayload}
                selectedPath={selectedDiffPath}
                onSelectedPathChange={setSelectedDiffPath}
              />
            )
              : <Empty description="该任务没有完整差异文本产物" />}
      </Drawer>
    </div>
  );
}

export { STEP_LABELS };
