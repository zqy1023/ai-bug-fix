import type { StepRecord, TaskDetailResponse } from '../../lib/types';
import { taskIsActive } from '../../lib/lifecycle';
import { workflowSteps, type EvidenceState } from './reviewEvidence';
import { STEP_LABELS } from './stages';

export type Dict = Record<string, unknown>;
/** 结论色调：与设计令牌一一对应，只做辅助编码，文字始终给出语义。 */
export type VerdictTone = 'ok' | 'bad' | 'warn' | 'info' | 'muted';

const PLACEHOLDERS = new Set(['-', '—', '–', 'n/a', 'na', 'none', 'null', 'undefined']);

export function dict(value: unknown): Dict {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Dict : {};
}

export function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

export function getText(source: Dict, ...keys: string[]): string {
  for (const key of keys) {
    const value = text(source[key]);
    if (value) return value;
  }
  return '';
}

export function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map((item) => String(item ?? '').trim()).filter((item) => item && !PLACEHOLDERS.has(item.toLowerCase()))
    : [];
}

export function objects(value: unknown): Dict[] {
  return Array.isArray(value)
    ? value.filter((item) => item !== null && typeof item === 'object' && !Array.isArray(item)).map((item) => item as Dict)
    : [];
}

export function shortText(value: string, maxChars = 220): string {
  const normalized = value.replace(/\s+/g, ' ').trim();
  return normalized.length <= maxChars ? normalized : `${normalized.slice(0, maxChars).trimEnd()}…`;
}

/** 同名步骤可能有多条尝试记录；结论一律取最近一条。 */
export function latestStep(steps: StepRecord[], name: string): StepRecord | undefined {
  return [...steps].reverse().find((step) => step.name === name);
}

export function stepSummary(step?: StepRecord): string {
  if (!step) return '';
  const output = dict(step.output);
  return getText(output, 'summary', 'reason', 'root_cause', 'terminal_reason') || text(step.summary) || text(step.error?.message);
}

export function labelOf(name?: string): string {
  if (!name) return '未记录';
  return STEP_LABELS[name] || name;
}

export type ChangeFile = { path: string; status: string; added: number; removed: number };

export interface ChangeView {
  files: ChangeFile[];
  count: number;
  added: number;
  removed: number;
  /** 确定性基线比对完成，文件清单可信。 */
  evidenceKnown: boolean;
  /** 核验结论回传但文件清单缺失：既不是“没有差异”，也不是“没执行”。 */
  listMissing: boolean;
  checkState: EvidenceState;
  claimedFiles: string[];
  step?: StepRecord;
}

export interface DeliveryView {
  step?: StepRecord;
  branch: string;
  commit: string;
  pushed: boolean;
  localBranch: boolean;
  landed: boolean;
  prepared: boolean;
  analysisOnly: boolean;
  reportPaths: string[];
  finalApprovalPending: boolean;
  deliveredCode: boolean;
  deliveredReport: boolean;
}

export interface VerificationView {
  verifyState: EvidenceState;
  regressionState: EvidenceState;
  syntaxState: EvidenceState;
  verifyText: string;
  verifyTone: VerdictTone;
  regressionText: string;
  regressionTone: VerdictTone;
  syntaxText: string;
  syntaxTone: VerdictTone;
  internalEvidenceMissing: boolean;
  qualityFailed: boolean;
  syntaxOnlyVerification: boolean;
}

export interface ReviewView {
  step?: StepRecord;
  verdict: string;
  summary: string;
  findings: string[];
  requiredChanges: string[];
}

export interface Verdict {
  tone: VerdictTone;
  badge: string;
  headline: string;
  description: string;
  stopLabel: string;
  stopReason: string;
  nextAction: string;
}

export interface EvidenceResolution {
  reviewStep?: StepRecord;
}

export interface TaskView {
  result: string;
  active: boolean;
  paused: boolean;
  recoveryFailed: boolean;
  attempt: number;
  stepCount: number;
  durationMs?: number;
  terminalStep: string;
  activeStep: string;
  lastExecutedStep: string;
  verdict: Verdict;
  changes: ChangeView;
  delivery: DeliveryView;
  verification: VerificationView;
  review: ReviewView;
  locateStep?: StepRecord;
  repairStep?: StepRecord;
  reviewStep?: StepRecord;
  deliveryStep?: StepRecord;
}

/** 结果落点：取消、跳过、人工处理与交付完成是四种不同结论，不能混读。 */
function verdictOf(input: {
  result: string;
  active: boolean;
  paused: boolean;
  recoveryFailed: boolean;
  resultFailed: boolean;
  finalApprovalPending: boolean;
  delivered: boolean;
  deliveredCode: boolean;
  analysisOnly: boolean;
  changedCount: number;
  changeEvidenceKnown: boolean;
}): Verdict {
  const {
    result, active, paused, recoveryFailed, resultFailed, finalApprovalPending,
    delivered, deliveredCode, analysisOnly, changedCount, changeEvidenceKnown,
  } = input;

  let headline: string;
  let description: string;
  if (active) {
    headline = '任务执行中';
    description = '';
  } else if (paused) {
    headline = '单步执行已完成';
    description = '';
  } else if (recoveryFailed) {
    headline = '驳回后的恢复失败';
    description = '审批请求已受理，但后台没有完成恢复执行。请核对失败原因；待审批记录仍保留，可修复环境后重新提交决策。';
  } else if (result === 'BLOCKED') {
    headline = '验证受阻';
    description = '已保留当前补丁与有效证据；排除具体受阻原因后重新验证，无需批准未验证修改。';
  } else if (resultFailed) {
    headline = '任务未完成';
    description = '任务未完成；请查看停止原因和失败节点。';
  } else if (result === 'CANCELLED') {
    headline = '任务已取消';
    description = '执行已被取消；已产出的记录保留，但本轮不再推进。';
  } else if (result === 'SKIPPED') {
    headline = '本任务已跳过，未执行修复';
    description = '本任务被跳过，没有进入修复流程；这不是失败结论。';
  } else if (result === 'REJECTED') {
    headline = '交付已打回';
    description = '人工复核打回了本次交付并撤回修复分支；请在审批记录中查看打回原因。';
  } else if (['MANUAL', 'INSUFFICIENT_EVIDENCE'].includes(result)) {
    headline = '需人工处理，未确认修复完成';
    description = '证据不足或超出自动修复范围，需人工处理；未视为修复失败，也未视为修复成功。';
  } else if (finalApprovalPending) {
    headline = delivered ? '修复分支已交付，等待人工复核' : '本地候选已准备，等待正式交付';
    description = delivered
      ? '修复分支已交付到目标仓库，等待你确认保留或打回；系统不会自动合并。'
      : '本地候选已准备，等待正式交付；系统不会自动合并。';
  } else if (analysisOnly && delivered) {
    headline = '分析报告已提交';
    description = '仅提交 final.json 分析报告，源码改动未随分支交付。';
  } else if (deliveredCode) {
    headline = '源码修改已提交';
    description = '已交付源码变更，等待你检查并手动合并。';
  } else if (analysisOnly && changedCount > 0 && changeEvidenceKnown) {
    headline = '源码有改动，但本次仅交付分析报告';
    description = `检测到 ${changedCount} 个源码文件有改动，但本次只交付分析报告。`;
  } else if (analysisOnly && changedCount > 0) {
    headline = '仅交付分析报告，源码声明尚未核验';
    description = `检测到 ${changedCount} 个修改声明，尚未核实真实差异；本次只交付分析报告。`;
  } else if (analysisOnly) {
    headline = '本次走分析报告交付路径';
    description = '本次仅交付分析报告，未提交源码修复。';
  } else if (changedCount > 0 && changeEvidenceKnown) {
    headline = '检测到代码改动，但尚未完成源码交付';
    description = '已发现源码差异，但尚无可靠的分支交付证据。';
  } else if (changedCount > 0) {
    headline = '修复 Agent 声明了改动，尚未完成差异核验';
    description = '修改文件尚未完成基线比对，不能确认实际差异。';
  } else if (['RUNNING', 'PENDING', 'QUEUED', 'WAITING_APPROVAL'].includes(result)) {
    headline = '任务仍在处理中';
    description = '任务仍在执行或等待人工决策。';
  } else if (delivered) {
    headline = '交付记录已生成';
    description = '交付记录已生成。';
  } else {
    headline = result ? '任务已结束' : '任务结果待确认';
    description = result ? '任务已结束；详情见下方各分区结果。' : '任务结果待确认。';
  }

  const tone: VerdictTone = recoveryFailed || resultFailed ? 'bad'
    : finalApprovalPending || ['BLOCKED', 'MANUAL', 'INSUFFICIENT_EVIDENCE', 'UNVERIFIED'].includes(result) ? 'warn'
      : active || paused ? 'info'
        : delivered || deliveredCode ? 'ok'
          : 'warn';

  const badge = recoveryFailed ? '恢复失败'
    : resultFailed ? '未完成'
      : active ? '执行中'
        : paused ? '单步已完成'
          : result === 'BLOCKED' ? '验证受阻'
            : ['MANUAL', 'INSUFFICIENT_EVIDENCE'].includes(result) ? '需人工处理'
              : result === 'CANCELLED' ? '已取消'
                : result === 'SKIPPED' ? '已跳过'
                  : result === 'REJECTED' ? '已打回'
                    : finalApprovalPending ? '待人工复核'
                      : deliveredCode ? '已交付'
                        : delivered ? '已交付'
                          : '已结束';

  return { tone, badge, headline, description, stopLabel: resultFailed ? '停止原因' : '结论说明', stopReason: '', nextAction: '' };
}

function verificationOf(evidence: EvidenceResolution): VerificationView {
  const output = dict(evidence.reviewStep?.output);
  const dimensions = dict(output.dimensions);
  const dimension = (key: string) => {
    const status = text(dict(dimensions[key]).status);
    return { text: status === 'passed' ? '通过' : status === 'failed' ? '未通过' : evidence.reviewStep ? '待确认' : '未执行',
      tone: (status === 'passed' ? 'ok' : status === 'failed' ? 'bad' : 'muted') as VerdictTone };
  };
  const syntax = dimension('syntax'), business = dimension('business'), scope = dimension('scope');
  const state: EvidenceState = evidence.reviewStep ? 'available' : 'not_executed';
  return { verifyState: state, regressionState: state, syntaxState: state,
    verifyText: business.text, verifyTone: business.tone, regressionText: scope.text, regressionTone: scope.tone,
    syntaxText: syntax.text, syntaxTone: syntax.tone, internalEvidenceMissing: false,
    qualityFailed: [syntax, business, scope].some(item => item.tone === 'bad'), syntaxOnlyVerification: false };
}

function changesOf(detail: TaskDetailResponse, evidence: EvidenceResolution): ChangeView {
  const steps = detail.task.steps ?? [];
  const repairStep = latestStep(steps, 'unified_fixer_agent') ?? latestStep(steps, 'repair_agent');
  const repair = dict(repairStep?.output);
  const review = dict(evidence.reviewStep?.output);
  const diffFiles = objects(review.diff_files).filter(file => text(file.path));
  const fileByPath = new Map<string, Dict>(diffFiles.map(file => [text(file.path), file] as const));
  const evidenceKnown = Array.isArray(review.diff_files) && Boolean(dict(review.evidence_version).baseline_id);
  const claimedFiles = strings(repair.changed_files);
  const displayPaths = evidenceKnown ? diffFiles.map(file => text(file.path)) : claimedFiles;
  const files: ChangeFile[] = displayPaths.map((path) => {
    const file = fileByPath.get(path);
    return {
      path,
      status: text(file?.status) || (evidenceKnown ? 'modified' : 'declared'),
      added: Number(file?.added || 0) || 0,
      removed: Number(file?.removed || 0) || 0,
    };
  });
  const totals: Dict = {};
  const added = Number(totals.added ?? files.reduce((sum, file) => sum + file.added, 0)) || 0;
  const removed = Number(totals.removed ?? files.reduce((sum, file) => sum + file.removed, 0)) || 0;
  return {
    files,
    count: files.length,
    added,
    removed,
    evidenceKnown,
    listMissing: false,
    checkState: evidenceKnown ? 'available' : 'not_executed',
    claimedFiles,
    step: evidence.reviewStep,
  };
}

function deliveryOf(detail: TaskDetailResponse, result: string, recoveryFailed: boolean): { view: DeliveryView; approvalStep?: StepRecord } {
  const steps = detail.task.steps ?? [];
  const deliveryStep = latestStep(steps, 'publish_delivery') ?? latestStep(steps, 'prepare_delivery');
  const approvalStep = latestStep(steps, 'request_approval');
  const delivery = dict(deliveryStep?.output);
  const deliveryInput = dict(deliveryStep?.input);
  const phase = detail.delivery?.phase;
  const landed = phase === 'published';
  const prepared = phase === 'prepared';
  const analysisOnly = deliveryInput.analysis_only === true;
  const finalApprovalPending = Boolean(
    !recoveryFailed && !analysisOnly && (prepared || landed) && (
      detail.task.approval_pending === true
      || approvalStep?.status === 'waiting'
      || result === 'WAITING_APPROVAL'
    ),
  );
  return {
    approvalStep,
    view: {
      step: deliveryStep,
      branch: detail.delivery?.branch || getText(delivery, 'repair_branch') || getText(deliveryInput, 'repair_branch'),
      commit: detail.delivery?.commit || getText(delivery, 'commit'),
      pushed: delivery.pushed === true || phase === 'published',
      localBranch: delivery.local_branch === true,
      landed,
      prepared,
      analysisOnly,
      reportPaths: strings(deliveryInput.report_paths),
      finalApprovalPending,
      deliveredCode: landed && !analysisOnly,
      deliveredReport: landed && analysisOnly,
    },
  };
}

/** 单一数据推导入口：结论、改动、交付、验证四块共用同一套取舍，避免各分区各算一遍。 */
export function buildTaskView(detail: TaskDetailResponse, evidence: EvidenceResolution): TaskView {
  const task = detail.task;
  const steps = workflowSteps(task.steps ?? []);
  const report = dict(task.report);
  const lifecycle = dict(task.lifecycle);
  const lifecycleStatus = getText(lifecycle, 'status').toLowerCase();
  const result = String(task.result || getText(report, 'result', 'status') || '').toUpperCase();
  const active = taskIsActive(task);
  const paused = lifecycleStatus === 'paused' || result === 'PAUSED';
  const activeStep = getText(lifecycle, 'current_step') || String(task.current_step || '');
  const attempt = Number(task.attempt ?? lifecycle.attempt ?? 0) || 0;
  const runError = dict(detail.run_error);
  const recoveryFailed = task.approval_pending === true
    && detail.run_status === 'failed'
    && getText(runError, 'status').toUpperCase() === 'FAILED';

  const verification = verificationOf(evidence);
  const changes = changesOf(detail, evidence);
  const { view: delivery } = deliveryOf(detail, result, recoveryFailed);

  const finalStep = latestStep(steps, 'finalize_execution');
  const terminalStep = active || paused
    ? activeStep
    : getText(lifecycle, 'terminal_step') || getText(report, 'terminal_step') || getText(finalStep?.output ?? {}, 'terminal_step') || '';
  const terminalReason = active || paused ? ''
    : (recoveryFailed ? getText(runError, 'terminal_reason', 'message') : '')
      || getText(lifecycle, 'terminal_reason') || getText(report, 'terminal_reason') || getText(finalStep?.output ?? {}, 'terminal_reason') || '';
  const nextAction = active || paused ? ''
    : (recoveryFailed ? getText(runError, 'next_action') : '')
      || getText(lifecycle, 'next_action') || getText(report, 'next_action') || getText(finalStep?.output ?? {}, 'next_action') || '';
  const resultFailed = !active && !paused
    && (recoveryFailed || ['FAILED', 'FAIL'].includes(result) || verification.qualityFailed || delivery.step?.status === 'failed');
  const stopReason = active || paused ? ''
    : terminalReason || [...steps].reverse().find((step) => step.status === 'failed')?.error?.message || '';

  const lastExecutedStep = [...steps]
    .filter((step) => step.status !== 'pending')
    .sort((left, right) => {
      const leftTime = Date.parse(left.finished_at || left.started_at || '') || 0;
      const rightTime = Date.parse(right.finished_at || right.started_at || '') || 0;
      return leftTime - rightTime;
    })
    .slice(-1)[0];

  const verdict = verdictOf({
    result,
    active,
    paused,
    recoveryFailed,
    resultFailed,
    finalApprovalPending: delivery.finalApprovalPending,
    delivered: delivery.landed,
    deliveredCode: delivery.deliveredCode,
    analysisOnly: delivery.analysisOnly,
    changedCount: changes.count,
    changeEvidenceKnown: changes.evidenceKnown,
  });
  verdict.stopReason = stopReason;
  verdict.nextAction = nextAction;

  const review = dict(evidence.reviewStep?.output);
  const reviewView: ReviewView = {
    step: evidence.reviewStep,
    verdict: getText(review, 'verdict', 'result') || String(evidence.reviewStep?.result || '').toUpperCase(),
    summary: getText(review, 'summary') || stepSummary(evidence.reviewStep),
    findings: strings(review.findings),
    requiredChanges: strings(review.required_changes),
  };

  return {
    result,
    active,
    paused,
    recoveryFailed,
    attempt,
    stepCount: workflowSteps(steps).length,
    durationMs: Number(report.elapsed_ms ?? task.duration_ms) || undefined,
    terminalStep,
    activeStep,
    lastExecutedStep: lastExecutedStep?.name || '',
    verdict,
    changes,
    delivery,
    verification,
    review: reviewView,
    locateStep: latestStep(steps, 'unified_fixer_agent') ?? latestStep(steps, 'locate_agent'),
    repairStep: latestStep(steps, 'unified_fixer_agent') ?? latestStep(steps, 'repair_agent'),
    reviewStep: evidence.reviewStep,
    deliveryStep: delivery.step,
  };
}

/** 顶部指标位：标签 + 主值 + 辅值，点击跳到对应分区。 */
export type HeroTileKey = 'changes' | 'verify' | 'regression' | 'review' | 'delivery';
export interface HeroTile {
  key: HeroTileKey;
  tab: 'root' | 'changes' | 'evidence' | 'delivery' | 'timeline';
  label: string;
  value: string;
  sub: string;
  tone: VerdictTone;
}

export function buildHeroTiles(view: TaskView): HeroTile[] {
  const { changes, verification, review, delivery } = view;
  const changeValue = changes.checkState === 'not_returned' ? '变更核验未回传'
    : changes.listMissing ? '文件清单未回传'
      : changes.count > 0
        ? changes.evidenceKnown ? `${changes.count} 个实际变更文件` : `${changes.count} 个文件声明待核验`
        : changes.evidenceKnown ? '已核验：无差异' : '暂无差异证据';
  const reviewValue = review.verdict === 'PASS' ? '通过'
    : review.verdict === 'FAIL' ? '未通过'
      : review.verdict === 'MANUAL' ? '需人工判断'
        : review.step ? review.verdict || '已执行' : '未执行';
  const reviewTone: VerdictTone = review.verdict === 'PASS' ? 'ok'
    : review.verdict === 'FAIL' ? 'bad'
      : review.step ? 'warn' : 'muted';
  const deliveryValue = delivery.finalApprovalPending ? '已交付 · 待复核'
    : delivery.landed ? delivery.analysisOnly ? '分析报告已交付' : '源码已交付'
      : delivery.prepared ? '本地候选已准备'
        : delivery.step?.status === 'failed' ? '交付失败'
          : delivery.branch || delivery.commit ? '有交付记录 · 待核验'
            : '未交付';
  const deliveryTone: VerdictTone = delivery.finalApprovalPending ? 'warn'
    : delivery.landed ? 'ok'
      : delivery.prepared ? 'warn'
        : delivery.step?.status === 'failed' ? 'bad'
          : delivery.branch || delivery.commit ? 'warn' : 'muted';

  return [
    {
      key: 'changes',
      tab: 'changes',
      label: '源码改动',
      value: changeValue,
      sub: changes.count > 0 && changes.evidenceKnown ? `+${changes.added} / −${changes.removed}` : changes.evidenceKnown ? '基线比对完成' : '未完成确定性比对',
      tone: changes.evidenceKnown ? 'ok' : changes.checkState === 'not_returned' ? 'warn' : 'muted',
    },
    { key: 'verify', tab: 'evidence', label: '业务修复', value: verification.verifyText, sub: 'Agent 源码审查', tone: verification.verifyTone },
    { key: 'regression', tab: 'evidence', label: '影响范围', value: verification.regressionText, sub: 'Agent 范围审查', tone: verification.regressionTone },
    { key: 'review', tab: 'evidence', label: 'AI 审查', value: reviewValue, sub: review.findings.length ? `${review.findings.length} 项发现` : '审查结论', tone: reviewTone },
    {
      key: 'delivery',
      tab: 'delivery',
      label: 'Git 交付',
      value: deliveryValue,
      sub: delivery.commit ? shortText(delivery.commit, 10) : '尚无提交',
      tone: deliveryTone,
    },
  ];
}

export function verificationSummary(view: TaskView): { tone: VerdictTone; text: string } {
  const { verify, regression, syntax } = {
    verify: view.verification.verifyText,
    regression: view.verification.regressionText,
    syntax: view.verification.syntaxText,
  };
  if (view.verification.qualityFailed) return { tone: 'bad', text: '验证未通过' };
  if (view.verification.internalEvidenceMissing) return { tone: 'warn', text: '审查内部证据未回传' };
  if (view.verification.verifyTone === 'warn') return { tone: 'warn', text: `问题验证 ${verify}` };
  if (view.verification.regressionTone === 'warn') return { tone: 'warn', text: `回归测试 ${regression}` };
  return { tone: 'ok', text: `业务修复 ${verify} · 影响范围 ${regression} · 语法 ${syntax}` };
}
