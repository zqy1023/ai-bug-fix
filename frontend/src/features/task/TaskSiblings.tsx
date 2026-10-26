import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Input, Skeleton } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import { workbenchQueries } from '../../lib/workbenchQueries';
import { shortId } from '../../lib/format';
import { StatusBadge } from '../../components/StatusBadge';
import styles from './TaskWorkspace.module.css';

export interface TaskSiblingsProps { readonly runId: string; readonly taskId: string }
export function TaskSiblings({ runId, taskId }: TaskSiblingsProps) {
  const run = useQuery(workbenchQueries.run(runId));
  const [search, setSearch] = useState('');
  const tasks = run.data?.tasks || [];
  const visible = tasks.filter((item) => `${item.task_id} ${item.carrier} ${item.biztype}`.toLowerCase().includes(search.toLowerCase()));
  return <aside className={styles.queue} aria-label="同批次任务">
    <header><strong>同批次任务</strong><span>{tasks.length}</span></header>
    <div className={styles.queueSearch}><Input aria-label="搜索同批次任务" prefix={<SearchOutlined />} placeholder="搜索任务、运输商…" allowClear value={search} onChange={(event) => setSearch(event.target.value)} /></div>
    {run.isLoading ? <Skeleton active paragraph={{ rows: 3 }} /> : run.isError ? <p className={styles.queueEmpty}>同批次任务读取失败</p> : visible.length ? <ul>
      {visible.map((item) => <li key={item.task_id}><Link to={`/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(item.task_id)}`} aria-current={item.task_id === taskId ? 'page' : undefined}>
        <span className={styles.queueTitle}><code>{shortId(item.task_id, 18)}</code><StatusBadge kind="task" value={item.approval_pending ? 'WAITING_APPROVAL' : item.result} /></span>
        <strong>{item.carrier || '未识别运输商'} · {item.biztype || '未知业务'}</strong>
        <small>{item.lifecycle?.terminal_reason || item.report?.change_summary || item.current_step || '查看任务证据与结果'}</small>
      </Link></li>)}
    </ul> : <p className={styles.queueEmpty}>{search ? '没有匹配的任务' : '本批次尚未返回任务索引'}</p>}
    <footer>{run.data?.worker_active ? '批次持续推进中' : run.data?.terminal ? '本轮执行已结束' : '批次状态自动同步'}</footer>
  </aside>;
}
