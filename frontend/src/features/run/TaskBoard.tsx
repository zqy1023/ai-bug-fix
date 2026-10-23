import { useEffect, useState, type FC } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Input, Pagination, Skeleton } from 'antd';
import { ArrowRightOutlined, SearchOutlined } from '@ant-design/icons';
import type { BoardTask, WorkflowStage } from '../../lib/types';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { formatDuration } from '../../lib/format';
import { StatusBadge } from '../../components/StatusBadge';
import { EmptyState } from '../../components/EmptyState';

interface TaskBoardProps { runId: string; stages?: WorkflowStage[]; onOpen: (task: BoardTask) => void }
const FILTERS = [
  ['all', '全部'], ['active', '执行中'], ['approval', '待审批'], ['success', '成功'],
  ['manual', '需人工'], ['failed', '失败'], ['skipped', '已跳过'], ['cancelled', '已取消'], ['paused', '已暂停'], ['other', '其他'],
];
const ACTIVE = new Set(['RUNNING', 'RETRYING', 'PENDING', 'QUEUED']);

function state(task: BoardTask): string {
  return task.approval_pending ? 'WAITING_APPROVAL' : String(task.lifecycle?.status || task.result || 'PENDING').toUpperCase();
}

export const TaskBoard: FC<TaskBoardProps> = ({ runId, stages = [], onOpen }) => {
  const [params, setParams] = useSearchParams();
  const filter = FILTERS.some(([key]) => key === params.get('result')) ? params.get('result')! : 'all';
  const page = Math.max(1, Number(params.get('page')) || 1);
  const pageSize = [10, 20, 50, 100].includes(Number(params.get('size'))) ? Number(params.get('size')) : 20;
  const keyword = params.get('q') || '';
  const [search, setSearch] = useState(keyword);
  const update = (values: Record<string, string>) => {
    setParams((previous) => { const next = new URLSearchParams(previous); Object.entries(values).forEach(([key, value]) => next.set(key, value)); return next; }, { replace: true });
  };
  useEffect(() => { setSearch(keyword); }, [keyword]);
  useEffect(() => {
    if (search === keyword) return;
    const timer = setTimeout(() => update({ q: search, page: '1' }), 300);
    return () => clearTimeout(timer);
  }, [search, keyword]);
  const query = useQuery({
    queryKey: [...queryKeys.board(runId), { page, pageSize, filter, keyword }],
    queryFn: () => api.board(runId, { page, page_size: pageSize, category: filter, keyword }),
    refetchInterval: 5000,
  });
  const visible = query.data?.tasks ?? [];
  const counts: Record<string, number> = { ...query.data?.counts, all: query.data?.all_total ?? query.data?.total ?? 0 };
  const currentPage = query.data?.page ?? page;
  if (query.isError) return <Alert type="error" showIcon message="任务列表加载失败" action={<Button onClick={() => void query.refetch()}>重试</Button>} />;
  return (
    <section className="task-register" aria-label="任务列表">
      <div className="task-register-toolbar">
        <div className="task-register-filters" role="group" aria-label="任务状态筛选">
          {FILTERS.map(([key, label]) => <button key={key} type="button" aria-pressed={filter === key} className={filter === key ? 'is-selected' : ''} onClick={() => update({ result: key, page: '1' })}>{label}<span>{counts[key] ?? 0}</span></button>)}
        </div>
        <Input allowClear prefix={<SearchOutlined />} aria-label="搜索运输商或错误" placeholder="搜索运输商、业务类型或错误…" maxLength={160} value={search} onChange={(event) => setSearch(event.target.value)} />
      </div>
      {query.isLoading ? <Skeleton active paragraph={{ rows: 6 }} /> : visible.length ? <div className="task-register-scroll"><table className="task-register-table">
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
      <footer className="task-register-footer">
        <span>{query.isFetching ? '同步中 · ' : ''}共 {query.data?.total ?? 0} 项匹配 · 批次 {counts.all} 项</span>
        <Pagination current={currentPage} pageSize={pageSize} total={query.data?.total ?? 0} showSizeChanger showQuickJumper pageSizeOptions={[10, 20, 50, 100]} onChange={(next, size) => update({ page: String(size !== pageSize ? 1 : next), size: String(size) })} />
      </footer>
    </section>
  );
};
