import { InboxOutlined } from '@ant-design/icons';
import type { ReactNode } from 'react';

export function EmptyState({
  title, description, action, compact = false,
}: { title: string; description?: string; action?: ReactNode; compact?: boolean }) {
  return (
    <div className={'empty-state' + (compact ? ' is-compact' : '')}>
      <div className="empty-state-icon"><InboxOutlined /></div>
      <strong>{title}</strong>
      {description && <span>{description}</span>}
      {action && <div className="empty-state-action">{action}</div>}
    </div>
  );
}
