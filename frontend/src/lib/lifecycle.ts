import type { TaskDetailResponse, TaskSummary } from './types';

export const ACTIVE_RUN = new Set(['queued', 'running', 'pause_requested', 'cancel_requested']);
const ACTIVE_TASK = new Set(['RUNNING', 'RETRYING', 'QUEUED', 'PENDING']);
const FAILED_TASK = new Set(['FAILED', 'FAIL', 'REJECTED', 'PARTIAL_FAILED']);

/** 所有任务视图共用生命周期判定，历史节点失败不覆盖新一次执行。 */
export function taskState(task: Pick<TaskSummary, 'result' | 'lifecycle'>): string {
  return String(task.result || 'PENDING').toUpperCase();
}

export function taskIsActive(task: Pick<TaskSummary, 'result' | 'lifecycle'>): boolean {
  return ACTIVE_TASK.has(taskState(task));
}

export function taskHasFailed(task: Pick<TaskSummary, 'result' | 'lifecycle'>): boolean {
  return FAILED_TASK.has(taskState(task));
}

export function taskFailure(detail: TaskDetailResponse): string {
  if (taskIsActive(detail.task)) return '';
  return detail.task.lifecycle?.terminal_reason || detail.task.report.terminal_reason
    || [...detail.task.steps].reverse().find((step) => step.status === 'failed')?.error?.message
    || (taskHasFailed(detail.task) ? detail.run_error?.message : '') || '';
}

export function taskCanRetry(detail: TaskDetailResponse): boolean {
  return detail.actions?.retry.enabled === true;
}
