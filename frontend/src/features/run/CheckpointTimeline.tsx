import { useQuery } from '@tanstack/react-query';
import { Button, Skeleton } from 'antd';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { formatTime, shortId } from '../../lib/format';
import { EmptyState } from '../../components/EmptyState';
import { StatusDot } from '../../components/StatusBadge';
import { Panel } from '../../components/ui';
import styles from './CheckpointTimeline.module.css';

/** 只读 checkpoint 时间线：展示节点推进与中断，不提供状态回滚。 */
export function CheckpointTimeline({ runId }: { runId: string }) {
  const query = useQuery({
    queryKey: queryKeys.checkpoints(runId),
    queryFn: () => api.checkpoints(runId),
    refetchInterval: 15000,
  });

  if (query.isLoading) return <Panel title="恢复记录" flush><div className={styles.loading}><Skeleton active paragraph={{ rows: 5 }} /></div></Panel>;
  if (query.isError) {
    return (
      <Panel title="恢复记录">
        <EmptyState
          title="checkpoint 暂时不可读取"
          description="执行记录可能尚未产生 checkpoint，或图装配失败。"
          action={<Button onClick={() => void query.refetch()}>重试</Button>}
          compact
        />
      </Panel>
    );
  }
  const items = query.data?.items ?? [];
  if (!items.length) {
    return <Panel title="恢复记录"><EmptyState title="还没有 checkpoint" description="执行开始后每个节点边界都会留下检查点。" compact /></Panel>;
  }

  return (
    <Panel title={<>恢复记录<span className={styles.headNote}>只记录可以从哪里继续，不代表节点正在运行</span></>} flush>
      <ol className={styles.list}>
        {items.map((item, index) => (
          <li key={item.checkpoint_id ?? index} className={styles.item}>
            <span className={item.has_interrupt ? `${styles.dot} ${styles.dotInterrupt}` : styles.dot} aria-hidden="true" />
            <span className={styles.time}>{formatTime(item.created_at)}</span>
            <span className={styles.copy}>
              <span className={styles.title}>
                <strong>{item.node ?? '图起点'}</strong>
                {item.has_interrupt && <StatusDot tone="waiting">执行已挂起</StatusDot>}
              </span>
            </span>
            <span className={styles.meta}>
              checkpoint {shortId(item.checkpoint_id, 12)}
              {item.next_nodes.length > 0 && <> · 下一步 {item.next_nodes.join(', ')}</>}
            </span>
          </li>
        ))}
      </ol>
    </Panel>
  );
}
