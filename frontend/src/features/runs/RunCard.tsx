import { useQuery } from '@tanstack/react-query';
import { DownOutlined, RightOutlined, ArrowRightOutlined } from '@ant-design/icons';
import type { RunSummary } from '../../lib/types';
import { formatBatchTime, shortId } from '../../lib/format';
import { workbenchQueries } from '../../lib/workbenchQueries';
import { RunProgress } from '../../components/RunProgress';
import styles from './RunCard.module.css';

export interface RunCardProps {
  readonly run: RunSummary;
  readonly expanded: boolean;
  readonly onToggle: () => void;
  readonly onOpen: () => void;
}

const STATUS = {
  queued: ['排队中', 'neutral'], running: ['执行中', 'active'], waiting_approval: ['待审批', 'waiting'],
  succeeded: ['已完成', 'neutral'], partial_failed: ['部分失败', 'failed'], failed: ['失败', 'failed'],
  paused: ['已暂停', 'neutral'], pause_requested: ['暂停中', 'waiting'], cancelled: ['已停止', 'neutral'], cancel_requested: ['停止中', 'waiting'],
} as const;

export function RunCard({ run, expanded, onToggle, onOpen }: RunCardProps) {
  const detail = useQuery({ ...workbenchQueries.run(run.run_id), enabled: expanded });
  const view = detail.data;
  const latestStep = view?.steps?.at(-1);
  const currentTask = !run.terminal && view?.tasks?.find((task) => task.task_id === view.run_lifecycle?.current_task_id);
  const terminalMessage = view?.run_report?.terminal_reason || view?.run_error?.message || view?.run_lifecycle?.terminal_reason;
  const latest = (run.terminal ? terminalMessage : view?.run_lifecycle?.progress?.message)
    || terminalMessage || latestStep?.assessment?.summary || latestStep?.summary
    || (run.waiting_tasks ? `${run.waiting_tasks} 个任务等待人工交付审批` : run.task_count === 0 ? '尚未派发问题任务，查看执行案卷了解当前进展' : `${run.task_count} 个任务的执行记录已同步`);
  const [label, tone] = STATUS[run.status] || ['状态未知', 'neutral'];
  return <><tr className={`${styles.row}${expanded ? ` ${styles.selected}` : ''}`}>
    <td><div className={styles.identity}>
      <button type="button" className={styles.expand} onClick={onToggle} aria-expanded={expanded} aria-controls={`run-latest-${run.run_id}`} aria-label={`${expanded ? '收起' : '展开'}批次 ${run.run_id}`}>{expanded ? <DownOutlined /> : <RightOutlined />}</button>
      <div><button type="button" className={styles.name} onClick={onOpen}>自动修复批次{run.status === 'running' && <i aria-hidden="true" />}</button><code className={styles.id} title={run.run_id}>{shortId(run.run_id, 25)}</code></div>
    </div></td>
    <td><span className={`${styles.status} ${styles[tone]}`}><i aria-hidden="true" />{label}</span></td>
    <td className={styles.progress}><RunProgress run={run} list /></td>
    <td className={styles.center}>{run.waiting_tasks > 0 ? <span className={styles.waiting}>{run.waiting_tasks}</span> : <span className={styles.zero}>0</span>}</td>
    <td className={styles.time} title={run.started_at}>{formatBatchTime(run.started_at)}</td>
    <td className={styles.last}><button type="button" className={styles.open} onClick={onOpen}>{run.terminal ? '回溯' : '详情'}</button></td>
  </tr>{expanded && <tr className={styles.expandedRow}><td colSpan={6}>
    <div className={styles.latest} id={`run-latest-${run.run_id}`}>
      <span className={styles.latestLabel}>最近动作</span>
      <span className={styles.latestText}>{detail.isLoading ? '正在读取批次最新记录…' : detail.isError ? '最新记录读取失败' : latest}{currentTask && <> · 当前任务 <code>{currentTask.task_id}</code></>}</span>
      {detail.isError ? <button className={styles.open} onClick={() => void detail.refetch()}>重试</button> : <button type="button" className={styles.open} onClick={onOpen}>进入批次详情 <ArrowRightOutlined /></button>}
    </div>
  </td></tr>}</>;
}
