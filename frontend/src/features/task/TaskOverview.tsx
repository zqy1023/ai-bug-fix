import { WorkbenchDrawer } from '../../components/ui/WorkbenchDrawer';
import { STOP_KIND_LABELS, stepStopKind, toneForStep, type StepTone as Tone } from '../../lib/stepPresentation';
import { useMemo, useState } from 'react';
import { App, Button,  Empty, Skeleton, Tag, Tooltip } from 'antd';
import {
  ArrowRightOutlined, CopyOutlined, DiffOutlined, GitlabOutlined, WarningOutlined,
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import type { StepRecord, TaskDetailResponse } from '../../lib/types';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { formatDuration } from '../../lib/format';
import { StatusBadge } from '../../components/StatusBadge';
import { Banner, KeyValueList, Panel } from '../../components/ui';
import { CodeDiffViewer } from '../../components/CodeDiffViewer';
import { taskIsActive } from '../../lib/lifecycle';
import { STEP_LABELS } from './stages';
import {
  EVIDENCE_STATE_LABELS, REVIEW_TOOL_NAMES, evidenceState, reviewSubsteps, substepToRecord, toStepRecord,
  type EvidenceState, type ReviewToolName,
} from './reviewEvidence';
import styles from './TaskOverview.module.css';

type Dict = Record<string, unknown>;

/** 当前业务阶段；审查内部工具不作为独立阶段。 */
const TASK_PHASE_GROUPS: Array<{ key: string; label: string; members: string[] }> = [
  { key: 'prepare', label: '准备', members: ['repair_branch_preflight', 'prepare_source_snapshot', 'prepare_workspace', 'prepare_evidence'] },
  { key: 'locate', label: '定位', members: ['locate_agent'] },
  { key: 'claim', label: '领取', members: ['claim_repair_slot', 'define_acceptance', 'reproduce_baseline'] },
  { key: 'repair', label: '修复', members: ['repair_agent'] },
  { key: 'review', label: '审查', members: ['review_agent'] },
  { key: 'decide', label: '决策', members: ['decide_validation'] },
  { key: 'deliver', label: '交付', members: ['prepare_analysis', 'prepare_delivery', 'publish_delivery', 'request_approval', 'finalize_execution'] },
];

type StageState = 'done' | 'current' | 'failed' | 'future' | 'stopped';

function stageStateOf(stage: { members: string[] }, steps: StepRecord[], activeStep: string, taskActive: boolean): StageState {
  const present = stage.members
    .map((name) => stepOf(steps, name))
    .filter((step): step is StepRecord => Boolean(step));
  if (!present.length) return 'future';
  if (present.some((step) => toneForStep(step) === 'danger')) return 'failed';
  // 取消与跳过都不是完成，也不是失败，单列一档以免被读成“已通过”。
  if (present.some((step) => toneForStep(step) === 'cancelled')) return 'stopped';
  if (present.some((step) => step.status === 'running' || step.status === 'waiting')) return 'current';
  if (present.every((step) => toneForStep(step) === 'skipped')) return 'stopped';
  if (taskActive && stage.members.includes(activeStep)) return 'current';
  return 'done';
}

const STAGE_CLASS: Record<StageState, string> = {
  done: 'stageDone', current: 'stageCurrent', failed: 'stageFailed', future: 'stage', stopped: 'stageStopped',
};

/** 阶段导航 tooltip 用：说明当前这一档到底是没跑、停了还是跑完了。 */
const STAGE_STATE_HINTS: Record<StageState, string> = {
  done: '已完成', current: '进行中', failed: '未通过', future: '未开始', stopped: '已停止或已跳过',
};

const MARK_CLASS: Record<Tone, string> = {
  success: 'markSuccess',
  danger: 'markDanger',
  running: 'markRunning',
  waiting: 'markWaiting',
  manual: 'markManual',
  neutral: '',
  pending: 'markPending',
  skipped: 'markSkipped',
  cancelled: 'markCancelled',
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

function toneText(tone: Tone): string {
  return ({
    success: '已完成',
    danger: '未通过',
    running: '执行中',
    waiting: '等待处理',
    manual: '需人工处理',
    skipped: '已跳过',
    cancelled: '已取消',
    neutral: '已记录，未作业务判定',
    pending: '未执行',
  })[tone];
}

function stepSummary(step?: StepRecord): string {
  if (!step) return '';
  const output = dict(step.output);
  return getText(output, 'summary', 'reason', 'root_cause', 'terminal_reason') || text(step.summary) || text(step.error?.message);
}

/** 证据结论：未执行与未回传必须显式区分，缺失不得被读成“通过”。 */
function evidenceVerdict(state: EvidenceState, passed: boolean, unverified: boolean): { text: string; tone: 'good' | 'bad' | 'muted' } {
  if (state !== 'available') return { text: EVIDENCE_STATE_LABELS[state], tone: 'muted' };
  if (passed) return { text: '通过', tone: 'good' };
  if (unverified) return { text: '未验证', tone: 'muted' };
  return { text: '未通过', tone: 'bad' };
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

const FILE_MARK: Record<string, string> = {
  added: 'markAdded', new: 'markAdded', created: 'markAdded',
  deleted: 'markDeleted', removed: 'markDeleted',
  modified: 'markModified', renamed: 'markModified', moved: 'markModified',
};

/** 变更文件行:状态点 + 标签 + 路径(mono) + 增删行数;点击路径打开差异。 */
function ChangedFileRow({ file, canOpenDiff, onOpen }: { file: Dict; canOpenDiff: boolean; onOpen: () => void }) {
  const status = String(file.status || 'modified').toLowerCase();
  const markClass = FILE_MARK[status] || 'markModified';
  return (
    <li className={styles.fileRow}>
      <span className={`${styles.fileMark} ${styles[markClass]}`} aria-hidden="true" />
      <span className={styles.fileStatus}>{fileStatusLabel(file.status)}</span>
      <button
        type="button"
        className={styles.filePathButton}
        title={text(file.path)}
        disabled={!canOpenDiff}
        onClick={onOpen}
      >
        <code>{text(file.path)}</code>
      </button>
      <span className={styles.fileLines}>
        {Number(file.added || 0) > 0 && <b className={styles.addNum}>+{Number(file.added)}</b>}
        {Number(file.removed || 0) > 0 && <b className={styles.removeNum}>−{Number(file.removed)}</b>}
      </span>
    </li>
  );
}

/** 指向某个步骤的详情入口；目标步骤不在步骤列表时禁用并说明原因，避免点开一个空面板。 */
function StepLinkButton({
  label, target, available, reason, onOpenStep,
}: {
  label: string; target: string; available: boolean; reason: string; onOpenStep: (stepName: string) => void;
}) {
  if (!available) {
    return (
      <Tooltip title={reason}>
        <span className={styles.disabledAction}>
          <Button size="small" type="link" disabled>{label}</Button>
        </span>
      </Tooltip>
    );
  }
  return <Button size="small" type="link" onClick={() => onOpenStep(target)}>{label}</Button>;
}

function resultHeadline(result: string, analysisOnly: boolean, deliveredCode: boolean, delivered: boolean, changedCount: number, changeEvidenceKnown: boolean, finalApprovalPending: boolean, executionFailed: boolean): string {
  if (result === 'BLOCKED') return '验证受阻';
  if (executionFailed || ['FAILED', 'FAIL'].includes(result)) return '任务未完成';
  // 取消、跳过与人工处理是三种不同的停点，不能和“已交付/已完成”混读。
  if (result === 'CANCELLED') return '任务已取消';
  if (result === 'SKIPPED') return '本任务已跳过，未执行修复';
  if (result === 'REJECTED') return '交付已打回';
  if (['MANUAL', 'INSUFFICIENT_EVIDENCE'].includes(result)) return '需人工处理，未确认修复完成';
  if (finalApprovalPending) return delivered ? '修复分支已交付，等待人工复核' : '本地候选已准备，等待正式交付';
  if (analysisOnly && delivered) return '分析报告已提交';
  if (deliveredCode) return '源码修改已提交';
  if (analysisOnly && changedCount > 0 && changeEvidenceKnown) return '源码有改动，但本次仅交付分析报告';
  if (analysisOnly && changedCount > 0) return '仅交付分析报告，源码声明尚未核验';
  if (analysisOnly) return '本次走分析报告交付路径';
  if (changedCount > 0 && changeEvidenceKnown) return '检测到代码改动，但尚未完成源码交付';
  if (changedCount > 0) return '修复 Agent 声明了改动，尚未完成差异核验';
  if (['RUNNING', 'PENDING', 'QUEUED', 'WAITING_APPROVAL'].includes(result)) return '任务仍在处理中';
  if (delivered) return '交付记录已生成';
  return result ? '任务已结束' : '任务结果待确认';
}

function resultDescription(analysisOnly: boolean, delivered: boolean, deliveredCode: boolean, changedCount: number, changeEvidenceKnown: boolean, finalApprovalPending: boolean, result: string, executionFailed: boolean): string {
  if (result === 'BLOCKED') return '已保留当前补丁与有效证据；排除具体受阻原因后重新验证，无需批准未验证修改。';
  if (executionFailed || result === 'FAILED' || result === 'FAIL') return '任务未完成；请查看停止原因和失败节点。';
  if (result === 'CANCELLED') return '执行已被取消；已产出的记录保留，但本轮不再推进。';
  if (result === 'SKIPPED') return '本任务被跳过，没有进入修复流程；这不是失败结论。';
  if (result === 'REJECTED') return '人工复核打回了本次交付并撤回修复分支；请在审批记录中查看打回原因。';
  if (result === 'MANUAL' || result === 'INSUFFICIENT_EVIDENCE') return '证据不足或超出自动修复范围，需人工处理；未视为修复失败，也未视为修复成功。';
  if (finalApprovalPending) return delivered
    ? '修复分支已交付到目标仓库，等待你确认保留或打回；系统不会自动合并。'
    : '本地候选已准备，等待正式交付；系统不会自动合并。';
  if (analysisOnly && delivered) return '仅提交 final.json 分析报告，源码改动未随分支交付。';
  if (analysisOnly && changedCount > 0 && changeEvidenceKnown) return `检测到 ${changedCount} 个源码文件有改动，但本次只交付分析报告。`;
  if (analysisOnly && changedCount > 0) return `检测到 ${changedCount} 个修改声明，尚未核实真实差异；本次只交付分析报告。`;
  if (analysisOnly) return '本次仅交付分析报告，未提交源码修复。';
  if (deliveredCode) return `已交付 ${changedCount} 个源码变更文件，等待你检查并手动合并。`;
  if (changedCount > 0 && !changeEvidenceKnown) return '修改文件尚未完成基线比对，不能确认实际差异。';
  if (changedCount > 0) return '已发现源码差异，但尚无可靠的分支交付证据。';
  if (changeEvidenceKnown) return '基线比对未发现源码差异。';
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
  else if (completed.some((step) => toneForStep(step) === 'cancelled')) tone = 'cancelled';
  else if (completed.every((step) => toneForStep(step) === 'skipped')) tone = 'skipped';
  else if (completed.length) tone = 'success';

  const markClass = MARK_CLASS[tone];
  return (
    <div className={styles.stageItem}>
      <span className={markClass ? `${styles.stageMark} ${styles[markClass]}` : styles.stageMark} aria-hidden="true" />
      <div className={styles.stageCopy}>
        <strong>{title}</strong>
        <span>{hint || toneText(tone)}</span>
      </div>
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
  const taskActive = taskIsActive(task);
  const taskPaused = lifecycleStatus === 'paused' || result === 'PAUSED';
  const activeStep = getText(lifecycle, 'current_step') || String(task.current_step || '');
  const taskAttempt = Number(task.attempt ?? lifecycle.attempt ?? 0) || 0;
  const runError = dict(detail.run_error);
  const recoveryFailed = task.approval_pending === true
    && detail.run_status === 'failed'
    && getText(runError, 'status').toUpperCase() === 'FAILED';

  const locateStep = stepOf(steps, 'locate_agent');
  const repairStep = stepOf(steps, 'repair_agent');
  const reviewStep = stepOf(steps, 'review_agent');
  const deliveryStep = stepOf(steps, 'publish_delivery') ?? stepOf(steps, 'prepare_delivery');
  const approvalStep = stepOf(steps, 'request_approval');
  const finalStep = stepOf(steps, 'finalize_execution');

  // 审查 Agent 的内部工具通常不在步骤主表里；先从 review_agent 的输出读出内部工具清单，
  // 再按需取回归档的正式记录，取不到时明确区分“未执行”和“数据未回传”。
  const reviewSubstepList = useMemo(() => reviewSubsteps(reviewStep?.output), [reviewStep?.output]);
  const substepByName = useMemo(
    () => new Map(reviewSubstepList.map((item) => [item.name, item] as const)),
    [reviewSubstepList],
  );
  const missingToolPaths = useMemo(() => REVIEW_TOOL_NAMES.flatMap((name) => {
    if (stepOf(steps, name)) return [];
    const path = substepByName.get(name)?.recordPath ?? '';
    return path ? [{ name, path }] : [];
  }), [steps, substepByName]);
  const internalRecords = useQuery({
    queryKey: [...queryKeys.artifactContent(detail.run_id, '', detail.task.task_id), { internal: missingToolPaths }],
    queryFn: async () => {
      const entries = await Promise.all(missingToolPaths.map(async ({ name, path }) => {
        try {
          const response = await api.artifactContent(detail.run_id, path, detail.task.task_id);
          return [name, toStepRecord(response.content)] as const;
        } catch {
          // 归档缺失时保持空值，由证据区显示“未回传”，不伪造结果。
          return [name, undefined] as const;
        }
      }));
      return new Map(entries);
    },
    enabled: missingToolPaths.length > 0,
    staleTime: 60_000,
  });

  // 取值顺序：步骤主表 → 归档的完整记录 → 审查输出里的内部工具摘要。
  // 三者都取不到时，只能区分“审查没跑过”和“审查跑过但没回传”。
  const toolStep = (name: ReviewToolName): StepRecord | undefined =>
    stepOf(steps, name) ?? internalRecords.data?.get(name) ?? substepToRecord(substepByName.get(name));
  const toolSummary = (name: ReviewToolName): string => substepByName.get(name)?.summary || '';
  const checkStep = toolStep('check_changes');
  const verifyStep = toolStep('verify_issue');
  const regressionStep = toolStep('run_regression');
  const syntaxStep = toolStep('syntax_regression');
  const reviewRan = Boolean(reviewStep);
  const checkEvidence = evidenceState(checkStep, substepByName.get('check_changes'), reviewRan);
  const verifyEvidence = evidenceState(verifyStep, substepByName.get('verify_issue'), reviewRan);
  const regressionEvidence = evidenceState(regressionStep, substepByName.get('run_regression'), reviewRan);

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
  // 核验结论回传了，但文件级差异清单没有回来：既不是“没有差异”，也不是“没执行”。
  const changeListMissing = checkEvidence === 'available' && !changeEvidenceKnown && changedCount === 0;
  const gateTotals = dict(gate.totals);
  const added = Number(gateTotals.added ?? changedFiles.reduce((sum, file) => sum + Number(file.added || 0), 0)) || 0;
  const removed = Number(gateTotals.removed ?? changedFiles.reduce((sum, file) => sum + Number(file.removed || 0), 0)) || 0;

  const branch = detail.delivery?.branch || getText(delivery, 'repair_branch') || getText(deliveryStep?.input ?? {}, 'repair_branch');
  const commit = detail.delivery?.commit || getText(delivery, 'commit');
  const pushed = delivery.pushed === true;
  const localBranch = delivery.local_branch === true;
  const landed = detail.delivery?.phase === 'published';
  const prepared = detail.delivery?.phase === 'prepared';
  const analysisOnly = deliveryInput.analysis_only === true;
  const finalApprovalPending = Boolean(
    !recoveryFailed && !analysisOnly && (prepared || landed) && (
      task.approval_pending === true
      || approvalStep?.status === 'waiting'
      || result === 'WAITING_APPROVAL'
    ),
  );
  const deliveredCode = landed && !analysisOnly;
  const deliveredReport = landed && analysisOnly;
  const reportPaths = strings(deliveryInput.report_paths);
  const terminalStep = taskActive || taskPaused
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
  const terminalReason = taskActive || taskPaused ? ''
    : (recoveryFailed
      ? getText(runError, 'terminal_reason', 'message')
      : '') || getText(lifecycle, 'terminal_reason') || getText(report, 'terminal_reason') || getText(finalStep?.output ?? {}, 'terminal_reason') || '';
  const nextAction = taskActive || taskPaused ? ''
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
  const verifyPassed = verifyStep?.assessment ? verifyStep.assessment.outcome === 'passed' : verify.passed === true && !syntaxOnlyVerification;
  const regressionSyntaxOnly = getText(regression, 'status') === 'syntax_only' || getText(regression, 'mode') === 'syntax';
  const regressionPassed = regressionStep?.assessment ? regressionStep.assessment.outcome === 'passed' : regression.passed === true && !regressionSyntaxOnly;
  const verifyUnverified = verifyStep?.assessment?.outcome === 'inconclusive' || getText(verify, 'status') === 'unverified';
  const regressionUnverified = regressionStep?.assessment?.outcome === 'inconclusive' || getText(regression, 'status') === 'unverified';
  const qualitySteps = [syntaxStep, verifyStep, regressionStep].filter(Boolean) as StepRecord[];
  const qualityFailed = qualitySteps.some((step) => toneForStep(step) === 'danger');
  // 内部证据缺失时不能推断验证结论，只能说明缺失本身。
  const verifyVerdict = evidenceVerdict(verifyEvidence, verifyPassed, verifyUnverified);
  const regressionVerdict = evidenceVerdict(regressionEvidence, regressionPassed, regressionUnverified);
  const verifyText = verifyEvidence !== 'available'
    ? EVIDENCE_STATE_LABELS[verifyEvidence]
    : syntaxOnlyVerification ? '仅语法检查' : verifyVerdict.text;
  const verifyTone = verifyEvidence !== 'available' || syntaxOnlyVerification ? 'muted' : verifyVerdict.tone;
  const regressionText = regressionEvidence !== 'available'
    ? EVIDENCE_STATE_LABELS[regressionEvidence]
    : regressionSyntaxOnly ? '仅语法编译' : regressionVerdict.text;
  const regressionTone = regressionEvidence !== 'available' || regressionSyntaxOnly ? 'muted' : regressionVerdict.tone;
  const internalEvidenceMissing = [checkEvidence, verifyEvidence, regressionEvidence].includes('not_returned');
  const resultFailed = !taskActive && !taskPaused
    && (recoveryFailed || ['FAILED', 'FAIL'].includes(result) || qualityFailed || deliveryStep?.status === 'failed');
  const terminalReasonFromFailure = recoveryFailed
    ? terminalReason || '驳回请求已受理，但后台恢复执行失败。'
    : terminalReason || (
      [...steps].reverse().find((step) => step.status === 'failed')?.error?.message || ''
    );
  const originalErrorPreview = originalError.split(/\r?\n/)[0] || originalError;
  const terminalRecord = terminalStep ? stepOf(steps, terminalStep) : undefined;

  // 差异产物可能挂在变更核验步骤上，也可能挂在审查步骤上，两处都要认。
  const diffArtifact = useMemo(() => (
    (checkStep?.artifacts || []).concat(reviewStep?.artifacts || [])
      .find((artifact) => artifact.name === 'diff' || artifact.path.endsWith('/diff.json'))
  ), [checkStep?.artifacts, reviewStep?.artifacts]);
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

  const stageStates = TASK_PHASE_GROUPS.map((stage) => stageStateOf(stage, steps, activeStep, taskActive));
  // 活跃任务必须有“当前”档:没有任何阶段命中当前节点时,推进到第一个未来档。
  const firstFuture = stageStates.indexOf('future');
  const navStates: StageState[] = stageStates.map((state, index) => {
    if (!taskActive || state !== 'future' || stageStates.includes('current')) return state;
    return index === firstFuture ? 'current' : state;
  });

  return (
    <div className={styles.overview}>
      <nav className={styles.stageNav} aria-label="执行阶段">
        {TASK_PHASE_GROUPS.map((stage, index) => (
          <span
            key={stage.key}
            className={`${styles.stage} ${styles[STAGE_CLASS[navStates[index]]]}`}
            title={`${stage.label}（${STAGE_STATE_HINTS[navStates[index]]}）：${stage.members.map((name) => STEP_LABELS[name] || name).join(' · ')}`}
          >
            <span className={styles.stageDot} aria-hidden="true" />
            {stage.label}
          </span>
        ))}
      </nav>

      <section className={`${styles.hero} ${resultFailed ? styles.heroDanger : deliveredCode || deliveredReport || finalApprovalPending ? styles.heroDelivered : ['MANUAL', 'INSUFFICIENT_EVIDENCE'].includes(result) ? styles.heroManual : ''}`}>
        <div className={styles.heroMain}>
          <div className={styles.eyebrow}>任务结果</div>
          <div className={styles.titleRow}>
            <h2>{taskActive
              ? lifecycleStatus === 'retrying' || result === 'RETRYING' ? '任务重试中' : '任务执行中'
              : taskPaused ? '单步执行已完成' : recoveryFailed ? '驳回后的恢复失败' : resultText}</h2>
            <StatusBadge kind="task" value={taskActive
              ? lifecycleStatus === 'retrying' || result === 'RETRYING' ? 'RETRYING' : 'RUNNING'
              : taskPaused ? 'PAUSED' : recoveryFailed ? 'FAILED' : finalApprovalPending ? 'WAITING_APPROVAL' : task.result} />

          </div>
          <p className={styles.description}>
            {taskActive
              ? `当前节点：${terminalLabel(activeStep)}${taskAttempt > 0 ? ` · 第 ${taskAttempt} 次尝试` : ''}。较早尝试的错误不会覆盖当前执行状态。`
              : taskPaused
                ? `节点 ${terminalLabel(activeStep)} 的单步执行已结束；当前 Run 没有自动继续。请按依赖顺序选择下一节点执行，或通过 Run 控制重新恢复整轮。`
                : recoveryFailed
                  ? '审批请求已受理，但后台没有完成恢复执行。请核对失败原因；待审批记录仍保留，可修复环境后重新提交决策。'
                  : description}
          </p>
          {terminalReasonFromFailure && (
            <div className={`${styles.reason}${resultFailed ? ` ${styles.reasonDanger}` : ''}`}>
              <span>{resultFailed ? '停止原因' : '结论说明'}</span>
              <div className={styles.reasonCopy}>
                <p>{shortText(terminalReasonFromFailure, 220)}</p>
                {terminalReasonFromFailure.length > 220 && (
                  <details className={styles.fold}>
                    <summary>查看完整原因</summary>
                    <p>{terminalReasonFromFailure}</p>
                  </details>
                )}
              </div>
            </div>
          )}
          {nextAction && (
            <div className={styles.nextAction}>
              <ArrowRightOutlined />
              <div className={styles.nextActionCopy}>
                <span>下一步</span>
                <strong>{shortText(nextAction, 150)}</strong>
                {nextAction.length > 150 && (
                  <details className={styles.fold}>
                    <summary>查看完整建议</summary>
                    <p>{nextAction}</p>
                  </details>
                )}
              </div>
            </div>
          )}
        </div>
        <div className={styles.facts}>
          <div className={styles.factPrimary}>
            <span>源码改动</span>
            <strong>{checkEvidence === 'not_returned'
              ? '变更核验未回传'
              : changeListMissing
                ? '变更核验已执行，文件清单未回传'
                : changedCount > 0
                  ? changeEvidenceKnown ? `${changedCount} 个实际变更文件` : `${changedCount} 个文件声明待核验`
                  : changeEvidenceKnown ? '已核验：没有真实差异' : '尚无可核验源码差异'}</strong>
            <small>{checkEvidence === 'not_returned'
              ? '审查内部证据缺失，不能确认实际差异'
              : changeListMissing
                ? '核验结论已回传，文件级差异没有随审查结论返回'
                : changedCount > 0 && changeEvidenceKnown ? `+${added} / −${removed} 行`
                  : changeEvidenceKnown ? '基线差异核验已完成' : '未完成确定性基线比对'}</small>
          </div>
          <div className={styles.factRow}>
            <span>{taskActive || taskPaused ? '当前节点' : '自动修复停止点'}</span>
            <strong>{terminalLabel(terminalStep)}{terminalRecord ? ` · ${STOP_KIND_LABELS[stepStopKind(terminalRecord)]}` : ''}</strong>
          </div>
          <div className={styles.factRow}>
            <span>最后执行步骤</span>
            <strong>{terminalLabel(lastExecutedStep?.name || '')}{lastExecutedStep ? ` · ${STOP_KIND_LABELS[stepStopKind(lastExecutedStep)]}` : ''}</strong>
          </div>
          <div className={styles.factRow}>
            <span>执行耗时</span>
            <strong>{formatDuration(Number(report.elapsed_ms ?? task.duration_ms) || undefined)}</strong>
          </div>
          <div className={styles.factRow}>
            <span>已记录步骤</span>
            <strong>{steps.length} 个</strong>
          </div>
          {taskAttempt > 0 && (
            <div className={styles.factRow}>
              <span>任务尝试</span>
              <strong>第 {taskAttempt} 次</strong>
            </div>
          )}
        </div>
      </section>

      <Panel
        flush
        title={(
          <span className={styles.titleStack}>
            <h3>处理路径</h3>
            <span className={styles.titleSub}>只展示业务阶段；完整原子节点与原始数据位于下方执行记录</span>
          </span>
        )}
        extra={terminalStep ? <span>自动修复停止点：{terminalLabel(terminalStep)}</span> : undefined}
      >
        <div className={styles.stageGrid} aria-label="任务执行阶段">
          <StageItem title="根因定位" steps={[locateStep]} hint={locateStep ? toneText(toneForStep(locateStep)) : '未执行'} />
          <StageItem title="代码修改" steps={[repairStep, checkStep]} hint={analysisOnly ? '未随报告分支交付源码' : changedCount ? (changeEvidenceKnown ? `${changedCount} 个实际变更文件` : `${changedCount} 个文件声明待核验`) : changeEvidenceKnown ? '基线比对未发现差异' : repairStep ? toneText(toneForStep(repairStep)) : '未执行'} />
          <StageItem title="验证与回归" steps={qualitySteps} hint={qualitySteps.length
            ? qualityFailed ? '验证未通过'
              : internalEvidenceMissing ? '审查内部证据未回传'
                : verifyStep?.result === 'UNVERIFIED' ? '业务未验证，需人工确认'
                  : '验证节点已执行'
            : '未执行'} />
          <StageItem title="AI 审查" steps={[reviewStep]} hint={reviewVerdict ? ({
            PASS: '审查通过，准备本地候选',
            FAIL: '审查未通过，触发回修',
            MANUAL: analysisOnly
              ? '仅交付报告，不进入源码审批'
              : finalApprovalPending
                ? landed
                  ? '修复分支已交付，等待人工复核'
                  : '本地候选已准备，等待正式交付'
                : landed
                  ? '源码分支已正式交付'
                  : '先准备本地候选再交付',
          } as Record<string, string>)[reviewVerdict] || reviewVerdict : '未执行'} />
          <StageItem title="Git 交付" steps={[deliveryStep]} hint={landed ? analysisOnly ? '分析报告分支已提交' : '修复分支已提交' : deliveryStep ? toneText(toneForStep(deliveryStep)) : '未交付'} />
        </div>
      </Panel>

      <div className={styles.cardGrid}>
        <Panel
          flush
          title={(
            <span className={styles.titleStack}>
              <h3>根因分析</h3>
              <span className={styles.titleSub}>先看结论，证据按需展开</span>
            </span>
          )}
          extra={category && !['carrier', 'unknown', 'none', 'null', '-'].includes(category.toLowerCase()) ? <Tag>{category}</Tag> : undefined}
        >
          {originalError && (
            <div className={styles.pad}>
              <details className={styles.foldWarn}>
                <summary>
                  <WarningOutlined /> 原始 Error
                  <span className={styles.origPreview}>{originalErrorPreview}</span>
                  <span className={styles.origExpand}>查看全文</span>
                </summary>
                <pre className={styles.origText}>{originalError}</pre>
              </details>
            </div>
          )}
          {rootCause || analysisSummary || facts.length > 0 || evidence.length > 0 || evidenceGaps.length > 0 ? (
            <div className={styles.pad}>
              <div className={styles.rootCause}>
                <label className={styles.label}>根因摘要</label>
                <p className={styles.rootCauseText}>{rootCause || analysisSummary || '尚未生成根因结论。'}</p>
              </div>
              {(rootCause.length > 220 || (analysisSummary && analysisSummary !== rootCause) || facts.length > 0 || evidence.length > 0 || evidenceGaps.length > 0) && (
                <details className={`${styles.fold} ${styles.foldBold}`}>
                  <summary>查看完整分析与证据（{facts.length + evidence.length + evidenceGaps.length + (rootCause.length > 220 ? 1 : 0)} 项）</summary>
                  {rootCause.length > 220 && (
                    <div className={styles.detailBlock}>
                      <label>完整根因</label>
                      <p>{rootCause}</p>
                    </div>
                  )}
                  {analysisSummary && analysisSummary !== rootCause && (
                    <div className={styles.detailBlock}>
                      <label>定位摘要</label>
                      <p>{analysisSummary}</p>
                    </div>
                  )}
                  {facts.length > 0 && (
                    <div className={styles.detailBlock}>
                      <label>定位依据</label>
                      <ul>{facts.map((fact, index) => <li key={`${index}-${fact}`}>{fact}</li>)}</ul>
                    </div>
                  )}
                  {evidence.length > 0 && (
                    <div className={styles.detailBlock}>
                      <label>源码证据（{evidence.length}）</label>
                      {evidence.map((item, index) => (
                        <div className={styles.sourceEvidence} key={`${text(item.path)}-${index}`}>
                          <code>{text(item.path) || '源码位置未记录'}{text(item.symbol) ? ` · ${text(item.symbol)}` : ''}</code>
                          {text(item.finding) && <p>{text(item.finding)}</p>}
                        </div>
                      ))}
                    </div>
                  )}
                  {scope && <div className={styles.metaChips}><span className={styles.metaChip}><b>建议范围</b>{scope.toLowerCase() === 'carrier' ? '单一承运商' : scope.toLowerCase() === 'project' ? '项目 / 共享层' : scope}</span></div>}
                  {evidenceGaps.length > 0 && (
                    <div className={styles.detailBlock}>
                      <label>证据缺口（{evidenceGaps.length}）</label>
                      <ul>{evidenceGaps.map((item) => <li key={item}>{item}</li>)}</ul>
                    </div>
                  )}
                </details>
              )}
            </div>
          ) : (
            <div className={styles.pad}>
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={locateStep
                  ? '定位步骤已执行，但没有产出根因结论'
                  : '定位步骤未执行，本任务没有定位记录'}
              />
            </div>
          )}
          {locateStep && (
            <div className={styles.actionsRow}>
              <button type="button" className={styles.textLink} onClick={() => onOpenStep('locate_agent')}>查看定位步骤 <ArrowRightOutlined /></button>
            </div>
          )}
        </Panel>

        <Panel
          flush
          title={(
            <span className={styles.titleStack}>
              <h3>代码修改</h3>
              <span className={styles.titleSub}>区分工作区改动与真正提交到分支的源码</span>
            </span>
          )}
          extra={(
            <Tag color={deliveredCode ? 'success' : changedCount && changeEvidenceKnown ? 'gold' : !changeEvidenceKnown && claimedFilePaths.length ? 'gold' : 'default'}>
              {deliveredCode ? '源码已提交' : analysisOnly ? '仅交付报告' : changedCount && changeEvidenceKnown ? '实际改动未交付' : !changeEvidenceKnown && claimedFilePaths.length ? '改动声明待核验' : changeEvidenceKnown ? '无实际差异' : '暂无改动记录'}
            </Tag>
          )}
        >
          {analysisOnly && (
            <div className={styles.pad}>
              <Banner tone="warn">本次仅提交分析报告；源码改动未随该分支交付。</Banner>
            </div>
          )}
          {repairStep && (getText(repair, 'summary', 'analysis') || stepSummary(repairStep)) && (
            <div className={styles.pad}>
              <details className={styles.fold}>
                <summary>查看修复说明</summary>
                <p className={styles.reviewFull}>{getText(repair, 'summary', 'analysis') || stepSummary(repairStep)}</p>
              </details>
            </div>
          )}
          {changedFiles.length > 0 ? (
            <>
              <div className={styles.countPad}>
                <div className={styles.countLine}>
                  <strong>{changeEvidenceKnown ? `${changedCount} 个真实变更文件` : `${changedCount} 个声明文件（待核验）`}</strong>
                  {changeEvidenceKnown && <span className={styles.addNum}>+{added}</span>}
                  {changeEvidenceKnown && <span className={styles.removeNum}>−{removed}</span>}
                </div>
              </div>
              <ul className={styles.fileList}>
                {changedFiles.slice(0, 3).map((file, index) => (
                  <ChangedFileRow
                    key={`${text(file.path)}-${index}`}
                    file={file}
                    canOpenDiff={Boolean(diffArtifact)}
                    onOpen={() => {
                      setSelectedDiffPath(text(file.path));
                      setDiffOpen(true);
                    }}
                  />
                ))}
              </ul>
              {changedCount > 3 && (
                <div className={styles.pad}>
                  <details className={styles.fold}>
                    <summary>其余 {changedCount - 3} 个文件</summary>
                    <ul className={styles.fileList}>
                      {changedFiles.slice(3).map((file, index) => (
                        <ChangedFileRow
                          key={`${text(file.path)}-more-${index}`}
                          file={file}
                          canOpenDiff={Boolean(diffArtifact)}
                          onOpen={() => {
                            setSelectedDiffPath(text(file.path));
                            setDiffOpen(true);
                          }}
                        />
                      ))}
                    </ul>
                  </details>
                </div>
              )}
            </>
          ) : (
            <div className={styles.pad}>
              <div className={styles.noChange}>
                <strong>{checkEvidence === 'not_returned'
                  ? '变更门禁证据未回传'
                  : changeListMissing
                    ? '变更核验已执行，但没有回传文件清单'
                    : changeEvidenceKnown ? '没有检测到真实源码差异' : claimedFilePaths.length ? '修改声明尚未核验' : '没有可核验的源码差异'}</strong>
                <span>{checkEvidence === 'not_returned'
                  ? '审查内部的变更核验已执行，但结果没有回传到本任务，不能据此判断是否存在真实差异。'
                  : changeListMissing
                    ? '核验结论已经回传，文件级差异清单没有随审查结论返回；请用“查看代码差异”核对逐行差异。'
                    : changeEvidenceKnown ? '确定性门禁已比对基线；未将模型声明当作实际修改。'
                      : claimedFilePaths.length ? '修复 Agent 声明了候选文件，但还没有确定性差异证据。'
                        : '任务可能只做根因分析，或在修改前已终止。'}</span>
              </div>
            </div>
          )}
          <div className={styles.actionsRow}>
            {diffArtifact && (
              <Button size="small" icon={<DiffOutlined />} onClick={() => setDiffOpen(true)}>查看代码差异</Button>
            )}
            <StepLinkButton
              label="变更门禁详情"
              target="check_changes"
              available={Boolean(stepOf(steps, 'check_changes'))}
              reason={checkEvidence === 'not_executed'
                ? '变更门禁未执行，本任务没有该步骤记录'
                : '变更门禁记录只存在于审查归档产物中，不在步骤列表里，无法在此打开'}
              onOpenStep={onOpenStep}
            />
          </div>
        </Panel>

        <Panel
          flush
          title={(
            <span className={styles.titleStack}>
              <h3>验证与审查</h3>
              <span className={styles.titleSub}>{analysisOnly
                ? '当前分支交付的是分析报告，不进入源码修复审批'
                : 'AI 审查通过后会直接交付修复分支；人工复核只在交付后决定保留或打回'}</span>
            </span>
          )}
          extra={reviewVerdict ? (
            <Tag color={reviewVerdict === 'PASS' ? 'success' : reviewVerdict === 'FAIL' ? 'error' : 'gold'}>{({
              PASS: 'AI 审查通过',
              FAIL: 'AI 审查未通过',
              MANUAL: analysisOnly ? '报告路径' : landed ? (finalApprovalPending ? '已交付，等待人工复核' : '已正式交付') : finalApprovalPending ? '等待正式交付' : '先准备候选再交付',
            } as Record<string, string>)[reviewVerdict] || reviewVerdict}</Tag>
          ) : undefined}
        >
          <div className={styles.qualityList}>
            <div className={styles.qualityRow}>
              <span>问题验证</span>
              <strong className={verifyTone === 'good' ? styles.qualityGood : verifyTone === 'bad' ? styles.qualityBad : styles.qualityMuted}>
                {verifyText}
              </strong>
              <small className={styles.qualityDetail}>
                {verifyEvidence === 'not_executed'
                  ? '审查内部的问题重放工具未执行'
                  : verifyEvidence === 'not_returned'
                    ? '审查内部证据未回传（见下方说明）'
                    : syntaxOnlyVerification
                      ? '未执行原始问题重放'
                      : getText(verify, 'mode') || toolSummary('verify_issue')}{getText(verify, 'reason') && !syntaxOnlyVerification ? ` · ${getText(verify, 'reason')}` : ''}
              </small>
            </div>
            <div className={styles.qualityRow}>
              <span>回归测试</span>
              <strong className={regressionTone === 'good' ? styles.qualityGood : regressionTone === 'bad' ? styles.qualityBad : styles.qualityMuted}>
                {regressionText}
              </strong>
              <small className={styles.qualityDetail}>
                {regressionEvidence === 'not_executed'
                  ? '审查内部的真实请求回归工具未执行'
                  : regressionEvidence === 'not_returned'
                    ? '审查内部证据未回传（见下方说明）'
                    : `${regression.command_count !== undefined ? `${String(regression.command_count)} 条命令` : toolSummary('run_regression') || '未记录命令数'}${getText(regression, 'reason') ? ` · ${getText(regression, 'reason')}` : ''}`}
              </small>
            </div>
          </div>
          <div className={styles.reviewBlock}>
            <label className={styles.label}>{analysisOnly ? 'AI 审查结论（报告路径）' : 'AI 审查结论（非最终审批）'}</label>
            {internalEvidenceMissing && (
              <Banner tone="warn">
                审查内部证据未回传：变更核验、原问题重放或真实请求回归的结果没有随审查结论返回，
                下方“未回传”不代表通过或未通过，请到执行记录或审查归档产物中核对。
              </Banner>
            )}
            {reviewStep ? (
              <>
                <p className={styles.reviewCompact}>{shortText(reviewSummary || '审查步骤已执行，但没有记录摘要。', 220)}</p>
                {(reviewSummary.length > 180 || findings.length > 0 || requiredChanges.length > 0) && (
                  <details className={`${styles.fold} ${styles.foldBold}`}>
                    <summary>展开完整审查内容{findings.length ? ` · ${findings.length} 项发现` : ''}</summary>
                    <p className={styles.reviewFull}>{reviewSummary || '未记录审查摘要。'}</p>
                    {findings.length > 0 && (
                      <div className={styles.detailBlock}>
                        <label>审查发现</label>
                        <ul className={styles.findingsList}>
                          {findings.map((finding) => <li key={finding}>{finding}</li>)}
                        </ul>
                      </div>
                    )}
                    {requiredChanges.length > 0 && (
                      <div className={styles.detailBlock}>
                        <label>需要处理的事项（{requiredChanges.length}）</label>
                        <ul>{requiredChanges.map((item) => <li key={item}>{item}</li>)}</ul>
                      </div>
                    )}
                  </details>
                )}
              </>
            ) : (
              <p className={styles.mutedLine}>审查步骤未执行，本任务没有 AI 审查结论；任务可能在修复、验证阶段前结束，或处于人工审查模式。</p>
            )}
          </div>
          <div className={styles.actionsRow}>
            <StepLinkButton
              label="问题验证详情"
              target="verify_issue"
              available={Boolean(stepOf(steps, 'verify_issue'))}
              reason={verifyEvidence === 'not_executed'
                ? '原问题重放未执行，本任务没有该步骤记录'
                : '验证记录只存在于审查归档产物中，不在步骤列表里，无法在此打开'}
              onOpenStep={onOpenStep}
            />
            <StepLinkButton
              label="回归详情"
              target="run_regression"
              available={Boolean(stepOf(steps, 'run_regression'))}
              reason={regressionEvidence === 'not_executed'
                ? '真实请求回归未执行，本任务没有该步骤记录'
                : '回归记录只存在于审查归档产物中，不在步骤列表里，无法在此打开'}
              onOpenStep={onOpenStep}
            />
            <StepLinkButton
              label="完整审查记录"
              target="review_agent"
              available={Boolean(reviewStep)}
              reason="审查步骤未执行，本任务没有该步骤记录"
              onOpenStep={onOpenStep}
            />
          </div>
        </Panel>

        <Panel
          flush
          title={(
            <span className={styles.titleStack}>
              <h3>Git 分支交付</h3>
              <span className={styles.titleSub}>分支里有什么、提交到哪里、能否核验</span>
            </span>
          )}
          extra={(
            <Tag color={landed ? 'success' : deliveryStep?.status === 'failed' ? 'error' : 'default'}>
              {landed ? (finalApprovalPending ? '已交付，等待人工复核' : '已正式交付') : prepared ? '本地候选已准备' : deliveryStep?.status === 'failed' ? '交付失败' : '未确认提交'}
            </Tag>
          )}
        >
          {deliveryStep ? (
            <>
              <div className={styles.destination}>
                <span className={styles.destinationIcon}><GitlabOutlined /></span>
                <div>
                  <label>{analysisOnly ? '分析报告分支' : '修复分支'}</label>
                  <code>{branch || '分支名未记录'}</code>
                </div>
              </div>
              <div className={styles.kvPad}>
                <KeyValueList items={[
                  { label: 'Commit SHA', value: commit || '未记录' },
                  { label: '交付位置', value: pushed ? '已推送远端' : prepared ? '隔离工作区本地候选，尚未正式交付' : localBranch ? '已写入本地仓库' : '未确认推送或落地', text: true },
                  { label: '提交内容', value: analysisOnly ? (reportPaths.join('、') || 'final.json 分析报告') : changedCount ? `${changedCount} 个源码文件` : '未记录源码文件清单', text: true },
                ]} />
              </div>
              {(branch || commit) && (
                <div className={styles.actionsRow}>
                  {branch && <Button size="small" icon={<CopyOutlined />} onClick={() => void copyBranch()}>复制分支名</Button>}
                  {commit && <Button size="small" icon={<CopyOutlined />} onClick={() => void copyCommit()}>复制 Commit SHA</Button>}
                  {deliveryStep && <Button size="small" type="link" onClick={() => onOpenStep(deliveryStep?.name ?? 'prepare_delivery')}>交付步骤记录</Button>}
                </div>
              )}
              <div className={`${styles.footnote} ${landed ? styles.footnoteGood : styles.footnoteWarn}`}>
                {landed
                  ? '系统没有自动合并。请在 Git 上核对变更并由你手动合并。'
                  : prepared
                    ? '当前仅准备本地候选；随后会正式交付此提交，再进入人工复核。'
                  : deliveryStep.status === 'failed'
                    ? stepSummary(deliveryStep) || '交付步骤失败，请查看节点输入、错误和日志。'
                    : '没有足够的提交证据证明分支已可靠落地。'}
              </div>
            </>
          ) : (
            <div className={styles.pad}>
              <div className={styles.noChange}>
                <strong>尚未进入分支交付</strong>
                <span>当前任务没有可核验的修复分支或报告分支提交。</span>
              </div>
            </div>
          )}
        </Panel>
      </div>

      <WorkbenchDrawer
        title={`代码差异 · ${text(diffArtifact?.path) || 'diff.json'}`}
        placement="right"
        width="min(1400px, 96vw)"
        open={diffOpen}
        onClose={() => setDiffOpen(false)}
        styles={{ body: { padding: 10, overflow: 'hidden' } }}
      >
        {diffQuery.isLoading ? <Skeleton active paragraph={{ rows: 8 }} />
          : diffQuery.isError ? <Empty description="差异产物读取失败，请到变更门禁步骤核对错误。" />
            : diffPayload ? (
              <CodeDiffViewer
                payload={diffPayload}
                selectedPath={selectedDiffPath}
                onSelectedPathChange={setSelectedDiffPath}
              />
            )
              : <Empty description={checkEvidence === 'available'
                ? '变更门禁已执行，但没有回传差异文本产物'
                : checkEvidence === 'not_returned'
                  ? '审查内部证据未回传，本任务没有可展示的差异文本'
                  : '变更门禁未执行，本任务没有差异产物'} />}
      </WorkbenchDrawer>
    </div>
  );
}

export { STEP_LABELS };
