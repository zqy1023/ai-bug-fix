import { ClockCircleOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { Skeleton } from 'antd';
import type { ApprovalItem } from '../../lib/types';
import { formatBatchTime, shortId } from '../../lib/format';
import { EmptyState } from '../../components/EmptyState';
import { Panel } from '../../components/ui';
import styles from './ApprovalQueue.module.css';

/** 待审批队列：上下键切换，选中项与右栏审阅区联动。 */
export function ApprovalQueue({
  items, selectedId, loading, onSelect,
}: {
  items: ApprovalItem[];
  selectedId: string;
  loading: boolean;
  onSelect: (id: string) => void;
}) {
  if (loading) {
    return (
      <Panel flush title={<span className={styles.headTitle}><SafetyCertificateOutlined /> 待审批任务</span>}>
        <div className={styles.loading}>
          {Array.from({ length: 3 }).map((_, index) => (
            <Skeleton key={index} active paragraph={{ rows: 2 }} />
          ))}
        </div>
      </Panel>
    );
  }

  if (!items.length) {
    return (
      <Panel flush title={<span className={styles.headTitle}><SafetyCertificateOutlined /> 待审批任务</span>}>
        <div className={styles.loading}>
          <EmptyState
            title="没有待审批交付"
            description="通过交付门禁的源码分支会出现在这里。"
            compact
          />
        </div>
      </Panel>
    );
  }

  return (
    <Panel
      flush
      title={<span className={styles.headTitle}><SafetyCertificateOutlined /> 待审批任务</span>}
      extra={<span className={styles.count}>{items.length} 项</span>}
    >
      <ul className={styles.list} role="listbox" aria-label="待审批交付队列">
        {items.map((item) => {
          const active = item.approval_id === selectedId;
          return (
            <li key={item.approval_id}>
              <button
                type="button"
                role="option"
                aria-selected={active}
                className={`${styles.item}${active ? ` ${styles.itemActive}` : ''}`}
                onClick={() => onSelect(item.approval_id)}
              >
                <span className={`${styles.mark}${active ? ` ${styles.markActive}` : ''}`} aria-hidden="true" />
                <span className={styles.copy}>
                  <strong>
                    {item.summary || `${item.carrier || '未识别运输商'} · ${item.biztype || '未知业务'}`}
                  </strong>
                  <span className={styles.summary}><code>{shortId(item.task_id, 22)}</code> · {item.carrier} / {item.biztype}</span>
                  <span className={styles.meta}>
                    <ClockCircleOutlined /> {formatBatchTime(item.created_at)}
                    <span className={styles.dot}>·</span>
                    修改文件 {item.changed_files?.length ?? 0}
                  </span>
                  <span className={styles.run}>{shortId(item.run_id, 25)}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      <p className={styles.hint}>使用 ↑ ↓ 切换待审交付；决策后条目移入历史。</p>
    </Panel>
  );
}
