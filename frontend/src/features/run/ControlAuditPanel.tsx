import { useQuery } from '@tanstack/react-query';
import { Button, Skeleton } from 'antd';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { formatTime } from '../../lib/format';
import { EmptyState } from '../../components/EmptyState';

/** 控制审计：所有生命周期与单步操作都在这里留痕。 */
export function ControlAuditPanel({ runId }: { runId: string }) {
  const query = useQuery({
    queryKey: queryKeys.controlAudit(runId),
    queryFn: () => api.controlAudit(runId),
    refetchInterval: 15000,
  });

  if (query.isLoading) return <Skeleton active paragraph={{ rows: 5 }} />;
  if (query.isError) {
    return <EmptyState title="控制审计暂时不可读取" action={<Button onClick={() => void query.refetch()}>重试</Button>} compact />;
  }
  const items = query.data?.items ?? [];
  if (!items.length) {
    return <EmptyState title="暂无控制操作" description="启动、暂停、恢复、停止与单步执行都会记录在这里。" compact />;
  }

  return (
    <ul className="audit-list">
      {items.map((item) => (
        <li key={item.id} className="audit-item">
          <span className="audit-time">{formatTime(item.created_at)}</span>
          <span className="audit-action">{item.action}</span>
          <span className="audit-actor">{item.actor}</span>
          {item.task_id && <code className="audit-task">{item.task_id}</code>}
          {item.reason && <span className="audit-reason" title={item.reason}>{item.reason}</span>}
        </li>
      ))}
    </ul>
  );
}
