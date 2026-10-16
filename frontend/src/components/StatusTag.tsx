import { Tag } from 'antd';
import type { RunStatus, StepStatus } from '../lib/types';

const labels: Record<string, string> = {
  queued: '排队中', accepted: '已受理', running: '执行中', waiting: '等待中',
  waiting_approval: '待审批', succeeded: '已完成', success: '成功', SUCCESS: '成功',
  completed: '已结束', COMPLETED: '已结束', partial_failed: '部分失败 / 需关注',
  failed: '失败', FAILED: '失败', cancelled: '已取消', cancel_requested: '等待停止',
  pending: '待执行', skipped: '已跳过', manual: '需人工介入', MANUAL: '需人工介入',
  MANUAL_REVIEW_REQUIRED: '需人工审查', PASS: '审查通过', FAIL: '审查未通过',
  LOCATED: '已定位根因', INSUFFICIENT_EVIDENCE: '证据不足', passed: '通过',
  rejected: '已拒绝', REJECTED: '已拒绝', PENDING: '待处理', APPROVE: '已批准',
  REJECT: '已拒绝', DELIVERED: '已交付', NOT_RUN: '未运行', INCOMPLETE: '未完成',
  STALE: '验证已过期', VERIFIED: '验证通过', FAILED_VALIDATION: '验证失败',
  claimed: '已领取', repaired: '已修改', verified: '验证通过', reviewed: '审查完成',
  not_started: '未开始', branch_created: '已创建分支', files_applied: '已应用文件',
  committed: '已提交', pushed: '已推送', expired: '已过期',
};
const colors: Record<string, string> = {
  queued: 'default', accepted: 'default', running: 'processing', waiting: 'warning',
  waiting_approval: 'warning', succeeded: 'success', success: 'success', SUCCESS: 'success',
  completed: 'success', COMPLETED: 'success', partial_failed: 'orange', failed: 'error',
  FAILED: 'error', cancelled: 'default', cancel_requested: 'warning', pending: 'default',
  skipped: 'default', manual: 'orange', MANUAL: 'orange', MANUAL_REVIEW_REQUIRED: 'orange',
  PASS: 'success', FAIL: 'error', LOCATED: 'success', INSUFFICIENT_EVIDENCE: 'warning',
  passed: 'success', rejected: 'error', REJECTED: 'error', PENDING: 'warning',
  REJECT: 'error', DELIVERED: 'success', INCOMPLETE: 'warning', STALE: 'warning',
  claimed: 'processing', repaired: 'processing', verified: 'success', reviewed: 'success',
  not_started: 'default', branch_created: 'processing', files_applied: 'processing',
  committed: 'success', pushed: 'success', expired: 'default',
};

export function statusLabel(value?: string | null): string {
  if (!value) return '未知';
  return labels[value] ?? labels[value.toUpperCase()] ?? value;
}

export function StatusTag({ status }: { status: RunStatus | StepStatus | string }) {
  return <Tag color={colors[status] ?? colors[status.toUpperCase()] ?? 'default'}>{statusLabel(status)}</Tag>;
}
