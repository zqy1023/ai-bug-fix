import type { ReactNode } from 'react';
import { App } from 'antd';
import { ArrowLeftOutlined, CopyOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import type { TaskDetailResponse } from '../../lib/types';
import { taskState } from '../../lib/lifecycle';
import { shortId } from '../../lib/format';
import { StatusBadge } from '../../components/StatusBadge';
import styles from './TaskHeader.module.css';

/** 任务页头:面包屑回退 + 承运商标题 + 状态徽标 + 复制任务 ID。 */
export function TaskHeader({ detail, actions }: { detail: TaskDetailResponse; actions?: ReactNode }) {
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
      <div className={styles.title}><h1>{task.carrier || '未识别承运商'} <span>修复任务</span></h1><button type="button" onClick={() => void copyTaskId()} title={taskId}><code>{shortId(taskId, 26)}</code><CopyOutlined /></button><span>{task.biztype || '未知业务'}</span>{serviceName && <span>{serviceName}</span>}</div>
    </div>
    <div className={styles.state}><StatusBadge kind="task" value={taskState(task)} />{actions}</div>
  </header>;
}
