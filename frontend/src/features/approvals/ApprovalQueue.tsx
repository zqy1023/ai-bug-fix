import { ClockCircleOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { Skeleton } from 'antd';
import type { ApprovalItem } from '../../lib/types';
import { formatTime } from '../../lib/format';
import { EmptyState } from '../../components/EmptyState';

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
      <div className="approval-queue is-loading">
        {Array.from({ length: 3 }).map((_, index) => (
          <Skeleton key={index} active paragraph={{ rows: 2 }} />
        ))}
      </div>
    );
  }

  if (!items.length) {
    return (
      <EmptyState
        title="没有待审批交付"
        description="通过交付门禁的源码分支会出现在这里。"
        compact
      />
    );
  }

  return (
    <div className="approval-queue" role="listbox" aria-label="待审批交付队列">
      <div className="approval-queue-head">
        <span><SafetyCertificateOutlined /> 待决策</span>
        <span className="approval-count">{items.length} 项</span>
      </div>
      <ul className="approval-queue-list">
        {items.map((item) => {
          const active = item.approval_id === selectedId;
          return (
            <li key={item.approval_id}>
              <button
                type="button"
                role="option"
                aria-selected={active}
                className={'approval-queue-item' + (active ? ' is-active' : '')}
                onClick={() => onSelect(item.approval_id)}
              >
                <span className="approval-queue-mark" />
                <span className="approval-queue-copy">
                  <strong>
                    {item.carrier || '未识别运输商'}
                    <span className="identity-separator">/</span>
                    {item.biztype || '未知业务'}
                  </strong>
                  <span className="approval-queue-meta">
                    <ClockCircleOutlined /> {formatTime(item.created_at)}
                    <span className="dot-sep">·</span>
                    修改文件 {item.changed_files?.length ?? 0}
                  </span>
                  <span className="approval-queue-run">run:{item.run_id}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      <p className="approval-queue-hint">使用 ↑ ↓ 切换待审交付</p>
    </div>
  );
}
