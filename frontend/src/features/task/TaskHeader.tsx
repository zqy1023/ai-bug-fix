import { App } from 'antd';
import { ArrowLeftOutlined, CopyOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import type { TaskDetailResponse } from '../../lib/types';
import { taskState } from '../../lib/lifecycle';
import { shortId } from '../../lib/format';
import { StatusBadge } from '../../components/StatusBadge';
import styles from './TaskHeader.module.css';

/** 任务页头:面包屑回退 + 承运商标题 + 状态徽标 + 复制任务 ID。 */
export function TaskHeader({ detail }: { detail: TaskDetailResponse }) {
  const { message } = App.useApp();
  const { task } = detail;
  const serviceName = String(task.problem?.service_name ?? '');
  const taskId = task.task_id || '';

  const copyTaskId = async () => {
    if (!taskId) return;
    try {
      await navigator.clipboard.writeText(taskId);
      message.success('任务 ID 已复制');
    } catch {
      message.warning('复制失败，请从完整任务 ID 中手动复制');
    }
  };

  return <header className={styles.header}>
    <div className={styles.copy}>
      <nav aria-label="任务位置"><Link to={`/runs/${encodeURIComponent(detail.run_id)}`}><ArrowLeftOutlined /> 返回批次 {shortId(detail.run_id, 20)}</Link><span>自动修复管线</span></nav>
      <div className={styles.title}><h1>{task.carrier || '未识别承运商'} · {task.biztype || '问题'}修复任务</h1><button type="button" onClick={() => void copyTaskId()} title={taskId}><code>{shortId(taskId, 26)}</code><CopyOutlined /></button>{serviceName && <span>{serviceName}</span>}</div>
    </div>
    <div className={styles.state}><StatusBadge kind="task" value={taskState(task)} /><small>{['MANUAL','INSUFFICIENT_EVIDENCE'].includes(String(task.result)) ? '分析记录已归档 · 需人工处理' : task.result === 'SKIPPED' ? '本任务未执行修复' : detail.delivery?.phase === 'prepared' ? '本地候选就绪 · 待交付' : detail.delivery?.phase === 'published' ? '正式交付已完成' : '状态以执行记录为准'}</small></div>
  </header>;
}
