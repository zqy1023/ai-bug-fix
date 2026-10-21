import { useMemo, useState, type FC } from 'react';
import { Button, Input } from 'antd';
import { ArrowRightOutlined, SearchOutlined } from '@ant-design/icons';
import type { BoardTask, WorkflowStage } from '../../lib/types';
import { formatDuration } from '../../lib/format';
import { StatusBadge } from '../../components/StatusBadge';
import { EmptyState } from '../../components/EmptyState';

interface TaskBoardProps { tasks: BoardTask[]; stages?: WorkflowStage[]; onOpen: (task: BoardTask) => void }
type Filter = 'all' | 'attention' | 'active' | 'ended';
const ACTIVE = new Set(['RUNNING', 'RETRYING', 'PENDING', 'QUEUED']);
const ATTENTION = new Set(['FAILED', 'FAIL', 'REJECTED', 'PARTIAL_FAILED', 'WAITING_APPROVAL', 'PAUSED']);

function state(task: BoardTask): string {
  return task.approval_pending ? 'WAITING_APPROVAL' : String(task.result || 'PENDING').toUpperCase();
}

export const TaskBoard: FC<TaskBoardProps> = ({ tasks, stages = [], onOpen }) => {
  const [filter, setFilter] = useState<Filter>('all');
  const [search, setSearch] = useState('');
  const counts = {
    all: tasks.length,
    attention: tasks.filter((task) => ATTENTION.has(state(task))).length,
    active: tasks.filter((task) => ACTIVE.has(state(task))).length,
    ended: tasks.filter((task) => !ACTIVE.has(state(task)) && !['WAITING_APPROVAL', 'PAUSED'].includes(state(task))).length,
  };
  const visible = useMemo(() => tasks.filter((task) => {
    const status = state(task);
    const matches = filter === 'all' || (filter === 'attention' ? ATTENTION.has(status)
      : filter === 'active' ? ACTIVE.has(status) : !ACTIVE.has(status) && !['WAITING_APPROVAL', 'PAUSED'].includes(status));
    return matches && [task.carrier, task.biztype, task.task_id, task.terminal_reason].join(' ').toLowerCase().includes(search.trim().toLowerCase());
  }).sort((left, right) => Number(ATTENTION.has(state(right))) - Number(ATTENTION.has(state(left)))), [tasks, filter, search]);

  if (!tasks.length) return <EmptyState title="正在等待任务清单" description="完成日志发现和运输商识别后，任务将在此显示。" compact />;
  const filters: Array<{ key: Filter; label: string }> = [
    { key: 'all', label: '全部任务' }, { key: 'attention', label: '需要处理' },
    { key: 'active', label: '正在执行' }, { key: 'ended', label: '已结束' },
  ];
  return (
    <section className="task-register" aria-label="任务列表">
      <div className="task-register-toolbar">
        <div className="task-register-filters" role="group" aria-label="任务状态筛选">
          {filters.map(({ key, label }) => <button key={key} type="button" aria-pressed={filter === key} className={filter === key ? 'is-selected' : ''} onClick={() => setFilter(key)}>{label}<span>{counts[key]}</span></button>)}
        </div>
        <Input allowClear prefix={<SearchOutlined />} aria-label="搜索运输商或错误" placeholder="搜索运输商、业务类型或错误…" value={search} onChange={(event) => setSearch(event.target.value)} />
      </div>
      {visible.length ? <div className="task-register-scroll"><table className="task-register-table">
        <thead><tr><th>运输商 / 业务</th><th>状态</th><th>当前阶段 / 停止点</th><th>执行摘要</th><th>操作</th></tr></thead>
        <tbody>{visible.map((task) => {
          const status = state(task);
          const failed = ['FAILED', 'FAIL', 'REJECTED', 'PARTIAL_FAILED'].includes(status);
          return <tr key={task.task_id} className={failed ? 'needs-attention' : ''}>
            <td><button type="button" className="task-register-name" onClick={() => onOpen(task)}>{task.carrier || '未识别运输商'}</button><small>{task.biztype || '未知业务类型'}</small></td>
            <td><StatusBadge kind="task" value={status} /></td>
            <td><span title={task.current_step || task.terminal_step}>{stages.find((stage) => stage.members.includes(task.current_step || task.terminal_step))?.title || task.current_step || task.terminal_step || '等待分派'}</span><small>{task.steps_count} 个步骤 · {formatDuration(task.duration_ms)}{(task.attempt ?? 0) > 1 ? ` · 第 ${task.attempt} 次尝试` : ''}</small></td>
            <td className="task-register-reason"><span title={task.terminal_reason}>{ACTIVE.has(status) ? '自动流程正在推进' : task.terminal_reason || task.next_action || '等待生成执行结论'}</span></td>
            <td><Button type={failed || task.approval_pending ? 'primary' : 'text'} size="small" icon={<ArrowRightOutlined />} iconPosition="end" onClick={() => onOpen(task)}>{failed ? '排查失败' : task.approval_pending ? '查看审批' : '查看任务'}</Button></td>
          </tr>;
        })}</tbody>
      </table></div> : <EmptyState title="没有匹配的任务" description="尝试修改筛选条件或搜索内容。" compact />}
      <footer className="task-register-footer">显示 {visible.length} / {tasks.length} 个任务<span>需要处理的任务优先展示</span></footer>
    </section>
  );
};
