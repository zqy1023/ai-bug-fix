import { Tag } from 'antd';
import type { RunStatus, StepStatus } from '../lib/types';
const labels: Record<string, string> = { queued: '排队中', running: '执行中', waiting_approval: '待审批', succeeded: '已完成', partial_failed: '部分失败', failed: '失败', cancelled: '已取消', pending: '待执行', waiting: '等待中', skipped: '已跳过' };
const colors: Record<string, string> = { queued: 'default', running: 'processing', waiting_approval: 'warning', succeeded: 'success', partial_failed: 'orange', failed: 'error', cancelled: 'default', pending: 'default', waiting: 'warning', skipped: 'default' };
export function StatusTag({ status }: { status: RunStatus | StepStatus | string }) {
  return <Tag color={colors[status] ?? 'default'}>{labels[status] ?? (status || '未知')}</Tag>;
}