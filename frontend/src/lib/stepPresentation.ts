import type { StepRecord } from './types';

export type StepTone = 'success' | 'danger' | 'running' | 'waiting' | 'manual' | 'neutral' | 'pending' | 'skipped' | 'cancelled';

/** 正式评估优先；兼容历史记录时，不将程序完成解释成业务通过。 */
export function toneForStep(step?: StepRecord): StepTone {
  if (!step) return 'pending';
  const execution = step.execution?.status ?? step.status;
  if (execution === 'failed' || step.assessment?.outcome === 'failed') return 'danger';
  // 取消既不是失败也不是未开始，必须单独成态，否则会和“从未执行”混在一起。
  if (execution === 'cancelled') return 'cancelled';
  if (execution === 'running') return 'running';
  if (execution === 'waiting') return 'waiting';
  if (step.assessment?.outcome === 'inconclusive') return 'manual';
  if (execution === 'skipped') return 'skipped';
  if (step.assessment?.outcome === 'passed') return 'success';
  if (step.assessment?.outcome === 'not_applicable') return 'neutral';
  const result = String(step.result ?? '').toUpperCase();
  if (['FAIL', 'FAILED', 'REJECTED'].includes(result)) return 'danger';
  if (['MANUAL', 'INSUFFICIENT_EVIDENCE', 'UNVERIFIED'].includes(result)) return 'manual';
  if (result === 'CANCELLED') return 'cancelled';
  if (result === 'SKIPPED') return 'skipped';
  return execution === 'succeeded' ? 'neutral' : 'pending';
}

/**
 * 停点语义：把“未执行”“已取消”“已跳过”“需人工处理”“业务未通过”拆成互不相同的几类。
 * MANUAL / INSUFFICIENT_EVIDENCE 属于人工处理结论，不算失败。
 */
export type StepStopKind =
  | 'not_executed' | 'running' | 'waiting' | 'cancelled' | 'skipped'
  | 'manual' | 'failed' | 'passed' | 'recorded';

export function stepStopKind(step?: StepRecord): StepStopKind {
  if (!step) return 'not_executed';
  const tone = toneForStep(step);
  if (tone === 'danger') return 'failed';
  if (tone === 'running') return 'running';
  if (tone === 'waiting') return 'waiting';
  if (tone === 'cancelled') return 'cancelled';
  if (tone === 'skipped') return 'skipped';
  if (tone === 'manual') return 'manual';
  if (tone === 'success') return 'passed';
  if (tone === 'neutral') return 'recorded';
  return 'not_executed';
}

export const STOP_KIND_LABELS: Record<StepStopKind, string> = {
  not_executed: '未执行',
  running: '执行中',
  waiting: '等待处理',
  cancelled: '已取消',
  skipped: '已跳过',
  manual: '需人工处理',
  failed: '业务未通过',
  passed: '已完成',
  recorded: '已记录，未作业务判定',
};
