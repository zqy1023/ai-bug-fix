import { useQuery } from '@tanstack/react-query';
import { Button, Skeleton } from 'antd';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { formatTime, shortId } from '../../lib/format';
import { EmptyState } from '../../components/EmptyState';

/** 只读 checkpoint 时间线：展示节点推进与中断，不提供状态回滚。 */
export function CheckpointTimeline({ runId }: { runId: string }) {
  const query = useQuery({
    queryKey: queryKeys.checkpoints(runId),
    queryFn: () => api.checkpoints(runId),
    refetchInterval: 15000,
  });

  if (query.isLoading) return <Skeleton active paragraph={{ rows: 5 }} />;
  if (query.isError) {
    return (
      <EmptyState
        title="checkpoint 暂时不可读取"
        description="执行记录可能尚未产生 checkpoint，或图装配失败。"
        action={<Button onClick={() => void query.refetch()}>重试</Button>}
        compact
      />
    );
  }
  const items = query.data?.items ?? [];
  if (!items.length) {
    return <EmptyState title="还没有 checkpoint" description="执行开始后每个节点边界都会留下检查点。" compact />;
  }

  return (
    <ol className="checkpoint-list">
      {items.map((item, index) => (
        <li key={item.checkpoint_id ?? index} className="checkpoint-item">
          <span className={'checkpoint-dot' + (item.has_interrupt ? ' has-interrupt' : '')} />
          <div className="checkpoint-copy">
            <div className="checkpoint-title">
              <strong>{item.node ?? '图起点'}</strong>
              {item.has_interrupt && <span className="checkpoint-flag">中断等待审批</span>}
            </div>
            <span className="checkpoint-meta">
              {formatTime(item.created_at)} · checkpoint {shortId(item.checkpoint_id, 12)}
              {item.next_nodes.length > 0 && ` · 下一步 ${item.next_nodes.join(', ')}`}
            </span>
          </div>
        </li>
      ))}
    </ol>
  );
}
