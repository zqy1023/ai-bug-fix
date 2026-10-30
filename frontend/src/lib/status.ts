import type { Tone } from './types';

export interface StatusMeta {
  label: string;
  tone: Tone;
}

const ACTIVE_RUN = new Set<string>(['queued', 'running', 'pause_requested', 'cancel_requested']);
const ENDED_RUN = new Set<string>(['succeeded', 'partial_failed', 'failed', 'cancelled']);

export function runStatus(status?: string | null): StatusMeta {
  const value = String(status ?? '').toLowerCase();
  if (value === 'queued') return { label: '未开始', tone: 'idle' };
  if (value === 'running' || value === 'queued' || value === 'pause_requested') {
    return { label: '执行中', tone: 'info' };
  }
  if (value === 'paused') return { label: '已暂停', tone: 'idle' };
  if (value === 'succeeded') return { label: '已完成', tone: 'ok' };
  if (value === 'partial_failed') return { label: '部分失败', tone: 'warn' };
  if (value === 'failed') return { label: '失败', tone: 'danger' };
  if (value === 'cancelled' || value === 'cancel_requested') return { label: '已停止', tone: 'idle' };
  if (value === 'waiting_approval') return { label: '执行中', tone: 'info' };
  return { label: '执行中', tone: 'info' };
}

export function taskStatus(result?: string | null, lifecycleStatus?: string | null): StatusMeta {
  const value = String(result || '').toUpperCase();
  const life = String(lifecycleStatus || '').toLowerCase();
  if (value === 'CHANGED_DELIVERED') return { label: '已改动交付', tone: 'ok' };
  if (value === 'UNCHANGED_DELIVERED') return { label: '无改动交付', tone: 'info' };
  if (life === 'paused' || value === 'PAUSED') return { label: '已暂停', tone: 'idle' };
  if (value === 'FAILED') {
    return { label: '失败', tone: 'danger' };
  }
  if (value === 'SKIPPED') return { label: '已跳过', tone: 'idle' };
  if (value === 'RUNNING') return { label: '执行中', tone: 'info' };
  if (!value || value === 'PENDING') return { label: '未开始', tone: 'idle' };
  if (life === 'running' || life === 'retrying') return { label: '执行中', tone: 'info' };
  return { label: '失败', tone: 'danger' };
}

export function isRunActive(status?: string | null): boolean {
  return ACTIVE_RUN.has(String(status ?? '').toLowerCase());
}

export function isRunEnded(status?: string | null): boolean {
  return ENDED_RUN.has(String(status ?? '').toLowerCase());
}

export function needsAttention(task: { result?: string | null; lifecycle?: { status?: string } | null }): boolean {
  const value = String(task.result || '').toUpperCase();
  return ['FAILED', 'FAIL', 'REJECTED'].includes(value);
}

export function attentionCount(run: {
  manual_tasks?: number;
  failed_tasks?: number;
  blocked_tasks?: number;
}): number {
  return run.failed_tasks ?? 0;
}

export function runProgress(run: {
  task_count?: number;
  succeeded_tasks?: number;
  failed_tasks?: number;
  manual_tasks?: number;
  blocked_tasks?: number;
  skipped_tasks?: number;
  waiting_tasks?: number;
  completed_tasks?: number;
  cancelled_tasks?: number;
}): { done: number; total: number; pct: number } {
  const total = Math.max(0, run.task_count ?? 0);
  const done = Math.min(
    total,
    run.completed_tasks ?? ((run.succeeded_tasks ?? 0) +
      (run.failed_tasks ?? 0) +
      (run.manual_tasks ?? 0) +
      (run.blocked_tasks ?? 0) +
      (run.skipped_tasks ?? 0) + (run.cancelled_tasks ?? 0)),
  );
  return { done, total, pct: total ? Math.round((done / total) * 100) : 0 };
}

export function taskState(task: { result?: string | null }): string {
  return String(task.result || 'PENDING').toUpperCase();
}
