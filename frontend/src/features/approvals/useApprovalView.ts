import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { ApprovalItem, StepRecord } from '../../lib/types';
import { useProblemLog } from '../../hooks/useProblemLog';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { workbenchQueries } from '../../lib/workbenchQueries';
import { toneForStep } from '../../lib/stepPresentation';
import { latestStep } from '../task/taskVerdict';
import type { Tone } from '../../components/section/Section';

export type ApprovalFile = {
  path: string;
  status: string;
  added: number;
  removed: number;
  problems: string[];
  /** 方案说明里对本次编辑的解释。 */
  note: string;
};

export type ApprovalCheck = {
  key: string;
  label: string;
  state: 'passed' | 'failed' | 'unknown';
  detail: string;
};

export interface ApprovalView {
  /** 确定性门禁是否给出了文件级证据；false 时文件清单只是方案声明。 */
  gateEvidence: boolean;
  files: ApprovalFile[];
  added: number;
  removed: number;
  checks: ApprovalCheck[];
  verdict: string;
  reviewSummary: string;
  findings: string[];
  requiredChanges: string[];
  recoveryText: string;
  recoveryTone: Tone;
  scopeText: string;
  /** 完整原始日志 message（identify_carrier 保留的原始记录），不截断。 */
  originalMessage: string;
  /** 从日志中提取的 error 字段；与 message 不同时单独标注，避免把提取结果当成全文。 */
  originalError: string;
  serviceName: string;
  diffArtifactPath: string;
  steps: StepRecord[];
  loading: boolean;
  failed: boolean;
  /** 上次审批恢复失败：决策条与结论条都要显式提示。 */
  recoveryFailure: string;
}

const DIMENSIONS = [
  { key: 'syntax', label: '语法检查' },
  { key: 'business', label: '业务修复检查' },
  { key: 'scope', label: '影响范围检查' },
] as const;

const RECOVERY_LABELS: Record<string, string> = {
  verified: '已验证',
  unverified: '未验证',
  failed: '失败',
  not_executed: '未执行',
};

function dict(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item ?? '').trim()).filter(Boolean) : [];
}

/** 审批页的唯一数据推导入口：文件清单、检查项、审查结论与门禁可信度一次算清。 */
export function useApprovalView(item: ApprovalItem): ApprovalView {
  const taskQuery = useQuery(workbenchQueries.task(item.run_id, item.task_id));
  const detail = taskQuery.data;
  const steps = detail?.task.steps ?? [];
  // 原始日志统一从共享 Hook 取：历史任务会回读 run 级去重产物。
  const log = useProblemLog(detail);
  const reviewStep = latestStep(steps, 'review_agent');
  const checkStep = latestStep(steps, 'check_changes');
  const payload = dict(reviewStep?.output?.payload ?? reviewStep?.output);
  const gate = dict(checkStep?.output?.payload ?? checkStep?.output);
  const gateFilesRaw = Array.isArray(gate.files) ? gate.files.map(dict) : null;
  const claim = dict(reviewStep?.output?.claim ?? gate.claim);
  const gateScope = dict(gate.scope);
  // 与任务详情页同一条判据：有 actual 清单且基线可比较，才算确定性门禁证据。
  const gateEvidence = Boolean(
    checkStep && Array.isArray(claim.actual)
    && (gateScope.diffable === true || Number(gateScope.baseline_files || 0) > 0),
  );

  const edits = item.change_plan?.edits ?? [];
  const view = useMemo(() => {
    const editNote = (path: string) => edits.find((edit) => edit.path === path)?.note || '';
    const evidenceByPath = new Map((gateFilesRaw ?? [])
      .filter((file) => text(file.path) && text(file.status).toLowerCase() !== 'truncated')
      .map((file) => [text(file.path), file] as const));

    const candidates: string[] = gateEvidence
      ? (Array.isArray(claim.actual) ? strings(claim.actual) : [])
      : item.changed_files ?? [];
    const fallback = candidates.length ? candidates
      : (gateFilesRaw ?? [])
        .filter((file) => !['unchanged', 'same'].includes(text(file.status).toLowerCase()))
        .map((file) => text(file.path))
        .filter(Boolean);

    const files: ApprovalFile[] = (fallback.length ? fallback : item.changed_files ?? []).map((path) => {
      const evidence = evidenceByPath.get(path);
      return {
        path,
        status: text(evidence?.status) || (gateEvidence ? 'modified' : 'declared'),
        added: Number(evidence?.added || 0) || 0,
        removed: Number(evidence?.removed || 0) || 0,
        problems: strings(evidence?.problems),
        note: editNote(path),
      };
    });

    const current = Number(payload.review_contract_version) >= 6 && !payload.contract_error;
    const dimensions = dict(payload.dimensions);
    const stale = Boolean(payload.stale || (reviewStep && latestStep(steps, 'repair_agent')
      && Date.parse(latestStep(steps, 'repair_agent')!.started_at || '') > Date.parse(reviewStep.started_at || '')));
    const checks: ApprovalCheck[] = DIMENSIONS.map(({ key, label }) => {
      const dimension = dict(dimensions[key]);
      const status = stale ? 'unverified' : current ? text(dimension.status) : '';
      return {
        key,
        label,
        state: status === 'passed' ? 'passed' : status === 'failed' ? 'failed' : 'unknown',
        detail: stale
          ? '代码或输入已变化，需要重新审查。'
          : text(dimension.reason) || (current ? '等待 Agent 核对源码与执行证据。' : '旧记录没有分项结论。'),
      };
    });

    const recovery = item.business_recovery;
    const recoveryTone: Tone = recovery?.status === 'verified' ? 'ok'
      : recovery?.status === 'failed' ? 'bad'
        : recovery ? 'warn' : 'muted';

    const problem = dict(detail?.task.problem);
    const diffArtifact = [...steps].reverse().flatMap((step) => step.artifacts || [])
      .concat(detail?.artifacts ?? [])
      .find((artifact) => artifact.name === 'diff' || /[/\\]diff\.json$/.test(artifact.path));

    const runError = dict(detail?.run_error);
    const recoveryFailure = detail?.task.approval_pending && text(runError.status).toUpperCase() === 'FAILED'
      ? text(runError.terminal_reason) || text(runError.message) || '后台恢复未完成，待审批记录仍保留。'
      : '';

    return {
      gateEvidence,
      files,
      added: files.reduce((sum, file) => sum + file.added, 0),
      removed: files.reduce((sum, file) => sum + file.removed, 0),
      checks,
      verdict: stale ? '' : current ? text(payload.verdict).toUpperCase() : '',
      reviewSummary: text(payload.summary) || text(reviewStep?.summary),
      findings: strings(payload.findings),
      requiredChanges: strings(payload.required_changes),
      recoveryText: recovery ? RECOVERY_LABELS[recovery.status] ?? '未记录' : '未记录',
      recoveryTone,
      scopeText: item.scope === 'carrier' ? '运输商范围' : '项目 / 共享范围',
      // 完整日志：本任务记录优先，历史任务回读归档；提取的 error 只作最后兜底。
      originalMessage: log.message || log.extracted,
      originalError: log.extracted,
      serviceName: text(problem.service_name),
      diffArtifactPath: diffArtifact?.path ?? '',
      steps,
      loading: taskQuery.isLoading,
      failed: taskQuery.isError,
      recoveryFailure,
    } satisfies ApprovalView;
  }, [claim, detail, edits, gateEvidence, gateFilesRaw, item, log, payload, reviewStep, steps, taskQuery.isError, taskQuery.isLoading]);

  return view;
}

/** 差异产物按需读取：只在抽屉打开或需要预读时启用。 */
export function useApprovalDiff(view: ApprovalView, runId: string, taskId: string, enabled: boolean) {
  return useQuery({
    queryKey: [...queryKeys.artifactContent(runId, view.diffArtifactPath, taskId), 'approval-diff', enabled],
    queryFn: () => api.artifactContent(runId, view.diffArtifactPath, taskId),
    enabled: enabled && Boolean(view.diffArtifactPath),
    staleTime: 60_000,
  });
}

/** 审批页跳回任务详情时使用同一套分区参数（execution → 时间线）。 */
export function taskStepLink(runId: string, taskId: string, stepName: string): string {
  return `/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(taskId)}?tab=timeline&step=${encodeURIComponent(stepName)}`;
}

export { toneForStep };
