import { Tag } from 'antd';
import type { RunStatus, StepStatus } from '../lib/types';
const labels: Record<string, string> = { queued: '排队中', running: '执行中', waiting_approval: '待审批', succeeded: '已完成', partial_failed: '部分失败', failed: '失败', cancelled: '已取消', pending: '待执行', waiting: '等待中', skipped: '已跳过', manual: '需人工介入', MANUAL: '需人工介入', MANUAL_REVIEW_REQUIRED: '需人工审查', PASS: '审查通过', FAIL: '审查未通过', LOCATED: '已定位根因', INSUFFICIENT_EVIDENCE: '证据不足', passed: '通过', failed: '失败', rejected: '已拒绝' };
const colors: Record<string, string> = { queued: 'default', running: 'processing', waiting_approval: 'warning', succeeded: 'success', partial_failed: 'orange', failed: 'error', cancelled: 'default', pending: 'default', waiting: 'warning', skipped: 'default', manual: 'orange', MANUAL: 'orange', MANUAL_REVIEW_REQUIRED: 'orange', PASS: 'success', FAIL: 'error', LOCATED: 'success', INSUFFICIENT_EVIDENCE: 'warning', passed: 'success', failed: 'error', rejected: 'error' };
export function StatusTag({ status }: { status: RunStatus | StepStatus | string }) {
  return <Tag color={colors[status] ?? 'default'}>{labels[status] ?? (status || '未知')}</Tag>;
}