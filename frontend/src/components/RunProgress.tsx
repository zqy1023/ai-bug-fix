import type { RunSummary } from '../lib/types';
import styles from './RunProgress.module.css';

export interface RunProgressProps { readonly run: RunSummary; readonly detailed?: boolean; readonly list?: boolean }

/** The API exposes mutually exclusive failure and blocked counters. */
export function runProgress(run: RunSummary) {
  const total = Math.max(0, run.task_count);
  const delivered = Math.min(total, Math.max(0, run.succeeded_tasks));
  const manual = Math.min(total - delivered, Math.max(0, run.manual_tasks));
  const failed = Math.min(total - delivered - manual, Math.max(0, run.failed_tasks) + Math.max(0, run.blocked_tasks));
  const waiting = Math.min(total - delivered - manual - failed, Math.max(0, run.waiting_tasks));
  const inactive = Math.min(total - delivered - manual - failed - waiting, Math.max(0, run.inactive_tasks || 0));
  return { total, finished: delivered + manual + failed + inactive, segments: [
    { key: 'success', label: '已完成', value: delivered },
    { key: 'manual', label: '分析报告 / 人工', value: manual },
    { key: 'danger', label: '失败 / 受阻', value: failed },
    { key: 'warning', label: '待审批', value: waiting },
    { key: 'inactive', label: '其他已结束', value: inactive },
    { key: 'pending', label: '尚未结束', value: total - delivered - manual - failed - waiting - inactive },
  ] };
}

export function RunProgress({ run, detailed = false, list = false }: Readonly<RunProgressProps>) {
  const progress = runProgress(run);
  return <div className={`${styles.root}${list ? ` ${styles.list}` : detailed ? ` ${styles.detailed}` : ''}`}>
    <div className={styles.caption}><span><b>{progress.finished}</b> / {progress.total} <span>{list ? progress.total === 0 ? '等待派发' : progress.finished === progress.total ? '全部处理完成' : '任务已处理' : '任务已结束'}</span></span>{progress.total > 0 && <small className={run.succeeded_tasks === progress.total ? styles.complete : undefined}>{Math.round(progress.finished / progress.total * 100)}%</small>}</div>
    <div className={styles.track} role="img" aria-label={`${progress.total} 个任务，${progress.finished} 个已结束，${run.waiting_tasks} 个待审批`}>
      {progress.segments.filter((s) => s.value > 0).map((segment) => <span key={segment.key} className={styles[segment.key]} style={{ width: `${segment.value / progress.total * 100}%` }} title={`${segment.label} ${segment.value}`} />)}
    </div>
    {detailed && <div className={styles.legend}>{progress.segments.filter((s) => s.value > 0).map((s) => <span key={s.key}><i className={styles[s.key]} />{s.value} {s.label}</span>)}</div>}
  </div>;
}
