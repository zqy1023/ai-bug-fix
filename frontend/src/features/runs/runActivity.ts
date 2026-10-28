import type { RunDetail, TaskSummary } from '../../lib/types';
import { STEP_LABELS } from '../task/stages';

const ACTIVE_TASK = new Set(['RUNNING', 'RETRYING']);
const nameOf = (name: string) => STEP_LABELS[name] || name || '等待节点记录';
const taskStatus = (task: TaskSummary) => String(task.lifecycle?.status || task.result || '').toUpperCase();
const RUN_STEP_LABELS: Record<string, string> = {
  sync_source_repo: '同步源码', discover_logs: '拉取日志', normalize_error: '读取并规范化日志', deduplicate_error: '日志去重',
};

function stepProgress(progress: NonNullable<NonNullable<RunDetail['run_lifecycle']>['progress']>): string {
  const counts = progress.counts;
  const parts: string[] = [];
  const addCount = (key: string, label: string, unit = '条'): void => {
    const value = counts[key];
    if (Number.isFinite(value) && value >= 0) parts.push(`${label} ${value.toLocaleString('zh-CN')} ${unit}`);
  };
  if (progress.step === 'sync_source_repo') {
    const bytes = counts.downloaded_bytes;
    if (Number.isFinite(bytes) && bytes >= 0) {
      const unit = bytes >= 1024 ** 3 ? 'GiB' : bytes >= 1024 ** 2 ? 'MiB' : bytes >= 1024 ? 'KiB' : 'B';
      const divisor = { GiB: 1024 ** 3, MiB: 1024 ** 2, KiB: 1024, B: 1 }[unit];
      parts.push(`已下载 ${(bytes / divisor).toFixed(unit === 'B' ? 0 : 1)} ${unit}`);
    }
    addCount('elapsed_seconds', '已用', '秒');
    addCount('timeout_seconds', '超时设置', '秒');
  } else if (['discover_logs', 'normalize_error', 'deduplicate_error'].includes(progress.step)) {
    addCount('es_hits', '已拉取');
    addCount('parsed', '已解析');
    addCount('unparseable', '无法解析');
    addCount('normalized_count', '已规范化');
    addCount('ignored_count', '已忽略');
    addCount('duplicate_count', '已去重');
    addCount('unique_count', '保留问题');
    // record_limit 是解析记录上限，不是 ES 命中总数，不能作为完成百分比的分母。
    if (counts.record_limit > 0) addCount('record_limit', '拉取上限');
  }
  return parts.join(' · ');
}

/** 当前任务的节点记录优先于批次历史进度，只有同一节点的进度才可复用。 */
export function runActivity(run: RunDetail) {
  const executing = !run.terminal && run.worker_active !== false
    && ['running', 'pause_requested', 'cancel_requested'].includes(run.status);
  const selected = run.tasks.find(task => task.task_id === run.run_lifecycle?.current_task_id);
  const task = executing
    ? (selected && ACTIVE_TASK.has(taskStatus(selected)) ? selected : run.tasks.find(item => ACTIVE_TASK.has(taskStatus(item))))
    : selected;
  const records = task?.steps || [];
  const attempt = task?.lifecycle?.attempt || task?.attempt;
  const currentRecords = records.filter(step => !attempt || !step.attempt || step.attempt === attempt);
  const node = task?.lifecycle?.current_step || task?.current_step
    || [...currentRecords].reverse().find(step => step.status === 'running')?.name
    || run.run_lifecycle?.current_step || run.phase || '';
  const step = [...(task ? currentRecords : run.steps)].reverse().find(item => item.name === node);
  const progress = run.run_lifecycle?.progress;
  const matchingProgress = progress?.step === node ? progress : undefined;
  const nodeRunning = executing && (!step || step.status === 'running');
  const progressText = nodeRunning && matchingProgress ? stepProgress(matchingProgress) : '';
  const completed = currentRecords.filter(item => ['succeeded', 'skipped'].includes(item.status)).length;
  const stoppedText = run.terminal ? '本轮已结束' : run.status === 'paused' ? '执行已暂停'
    : run.status === 'waiting_approval' ? '等待人工审批' : run.status === 'queued' ? '等待执行' : '当前没有活动执行';
  const message = executing
    ? matchingProgress?.message || step?.summary || '执行中'
    : run.run_error?.message || run.run_report?.terminal_reason || run.run_lifecycle?.terminal_reason || stoppedText;
  return {
    executing, nodeRunning, task, node, nodeLabel: step?.title || RUN_STEP_LABELS[node] || nameOf(node), step,
    status: executing ? ({ waiting: '等待处理', succeeded: '节点已完成', failed: '节点执行失败', skipped: '节点已跳过', pending: '等待执行', running: progressText ? (node === 'sync_source_repo' ? '下载中' : '拉取处理中') : '执行中' }[step?.status || 'running']) : stoppedText,
    message, progressText, completed, recorded: currentRecords.length,
    updatedAt: matchingProgress ? run.run_lifecycle?.progress_at : task?.lifecycle?.updated_at || run.run_lifecycle?.updated_at,

  };
}
