import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { DownOutlined, RightOutlined, ArrowRightOutlined } from '@ant-design/icons';
import type { RunSummary } from '../../lib/types';
import { formatBatchTime, formatDuration, formatTime, shortId } from '../../lib/format';
import { workbenchQueries } from '../../lib/workbenchQueries';
import { RunProgress } from '../../components/RunProgress';
import { runActivity } from './runActivity';
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
  const activity = view ? runActivity(view) : undefined;
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!expanded || !activity?.nodeRunning) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [expanded, activity?.nodeRunning, activity?.step?.started_at]);
  const started = Date.parse(activity?.step?.started_at || '');
  const elapsed = activity?.nodeRunning && Number.isFinite(started) ? Math.max(0, now - started) : activity?.step?.duration_ms;
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
      <div className={styles.activity} aria-live="polite" aria-label="当前执行进度">
        {detail.isLoading ? <span>正在读取批次最新记录…</span> : detail.isError ? <span>最新记录读取失败</span> : activity && <>
          <div className={styles.activityHead}>
            <span className={activity.nodeRunning ? styles.live : styles.idle}>{activity.status}</span>
            {activity.task && <strong>{activity.task.carrier || activity.task.task_id} · {activity.task.biztype || '问题任务'}</strong>}
            <span className={styles.node}>{activity.nodeLabel}</span>
            {elapsed != null && <span className={styles.activityMeta}>节点耗时 {formatDuration(elapsed)}</span>}
          </div>
          <p className={styles.activityMessage}>{activity.message}</p>
          {activity.progressText && <p className={styles.activityMessage} aria-label="步骤执行进度">{activity.progressText}</p>}
          <div className={styles.activityFoot}>
            {activity.task && <code>{activity.task.task_id}</code>}
            {activity.recorded > 0 && <span>本次已记录步骤：{activity.completed} / {activity.recorded} 已完成</span>}
            {activity.step?.attempt && <span>第 {activity.step.attempt} 次尝试</span>}
            {activity.updatedAt && <time>更新于 {formatTime(activity.updatedAt)}</time>}
          </div>
        </>}
      </div>
      {detail.isError ? <button className={styles.open} onClick={() => void detail.refetch()}>重试</button> : <button type="button" className={styles.open} onClick={onOpen}>进入批次详情 <ArrowRightOutlined /></button>}
    </div>
  </td></tr>}</>;
}
