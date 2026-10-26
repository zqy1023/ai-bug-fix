import { Link } from 'react-router-dom';
import type { TaskDetailResponse } from '../../lib/types';
import { formatTime, shortId } from '../../lib/format';
import { KeyValueList, Panel } from '../../components/ui';
import styles from './TaskWorkspace.module.css';

export interface TaskMetadataProps { readonly detail: TaskDetailResponse }
export function TaskMetadata({ detail }: TaskMetadataProps) {
  const { task, delivery } = detail;
  const current = task.steps.find((step) => step.name === (task.lifecycle?.current_step || task.current_step));
  const timeline = [...task.steps].filter((step) => step.status !== 'pending').slice(-6).reverse();
  return <aside className={styles.metadata} aria-label="任务元数据">
    <Panel title="任务元数据"><KeyValueList items={[
      { label: '任务编号', value: task.task_id },
      { label: '所属批次', value: <Link to={`/runs/${encodeURIComponent(detail.run_id)}`}>{shortId(detail.run_id, 18)}</Link> },
      { label: '业务类型', value: task.biztype || '未记录', text: true },
      { label: '当前阶段', value: task.approval_pending ? '人工审批（等待中）' : current?.title || task.current_step || '未记录', text: true },
      { label: '候选 Commit', value: delivery?.commit || '尚未生成' },
      { label: '目标分支', value: delivery?.branch || '尚未生成' },
      { label: '交付状态', value: delivery?.phase === 'published' ? '已正式交付' : delivery?.phase === 'prepared' ? '本地候选 · 待交付' : delivery?.phase === 'failed' ? '交付失败' : '尚未交付', text: true },
    ]} /></Panel>
    <Panel title="最近执行记录" extra={`${task.steps.length} 个节点`}>
      <ol className={styles.timeline}>{timeline.map((step, index) => <li key={`${step.name}-${index}`}>
        <strong>{step.title}</strong><time>{formatTime(step.finished_at || step.started_at)}</time><p>{step.assessment?.summary || step.summary || ({ running: '执行中', succeeded: '执行完成', failed: '执行失败', waiting: '等待处理', skipped: '已跳过' } as Record<string, string>)[step.status]}</p>
      </li>)}</ol>
      {!timeline.length && <p className={styles.queueEmpty}>暂无执行记录</p>}
    </Panel>
  </aside>;
}
