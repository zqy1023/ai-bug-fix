import type { StepRecord } from './types';

export type StepTone = 'success' | 'danger' | 'running' | 'waiting' | 'manual' | 'neutral' | 'pending' | 'skipped';

/** 正式评估优先；兼容历史记录时，不将程序完成解释成业务通过。 */
export function toneForStep(step?: StepRecord): StepTone {
  if (!step) return 'pending';
  const execution = step.execution?.status ?? step.status;
  if (execution === 'failed' || step.assessment?.outcome === 'failed') return 'danger';
  if (execution === 'running') return 'running';
  if (execution === 'waiting') return 'waiting';
  if (step.assessment?.outcome === 'inconclusive') return 'manual';
  if (execution === 'skipped') return 'skipped';
  if (step.assessment?.outcome === 'passed') return 'success';
  if (step.assessment?.outcome === 'not_applicable') return 'neutral';
  const result = String(step.result ?? '').toUpperCase();
  if (['FAIL', 'FAILED', 'REJECTED'].includes(result)) return 'danger';
  if (['MANUAL', 'INSUFFICIENT_EVIDENCE', 'UNVERIFIED'].includes(result)) return 'manual';
  return execution === 'succeeded' ? 'neutral' : 'pending';
}
