import { useQuery } from '@tanstack/react-query';
import { Button, Skeleton } from 'antd';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { formatTime } from '../../lib/format';
import { EmptyState } from '../../components/EmptyState';
import { Panel } from '../../components/ui';
import styles from './ControlAuditPanel.module.css';

/** 控制审计：所有生命周期与单步操作都在这里留痕。 */
export function ControlAuditPanel({ runId }: { runId: string }) {
  const query = useQuery({
    queryKey: queryKeys.controlAudit(runId),
    queryFn: () => api.controlAudit(runId),
    refetchInterval: 15000,
  });

  if (query.isLoading) return <Panel title="操作审计" flush><div className={styles.loading}><Skeleton active paragraph={{ rows: 5 }} /></div></Panel>;
  if (query.isError) {
    return <Panel title="操作审计"><EmptyState title="控制审计暂时不可读取" action={<Button onClick={() => void query.refetch()}>重试</Button>} compact /></Panel>;
  }
  const items = query.data?.items ?? [];
  if (!items.length) {
    return <Panel title="操作审计"><EmptyState title="暂无控制操作" description="启动、暂停、恢复、停止与单步执行都会记录在这里。" compact /></Panel>;
  }

  return (
    <Panel title={<>操作审计<span className={styles.headNote}>人工操作与系统控制动作</span></>} flush>
      <ul className={styles.list}>
        {items.map((item) => (
          <li key={item.id} className={styles.item}>
            <span className={styles.time}>{formatTime(item.created_at)}</span>
            <span className={styles.actor}>{item.actor}</span>
            <span className={styles.action}>{item.action}</span>
            <code className={styles.task} title={item.task_id}>{item.task_id}</code>
            <span className={styles.reason} title={item.reason}>{item.reason}</span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
