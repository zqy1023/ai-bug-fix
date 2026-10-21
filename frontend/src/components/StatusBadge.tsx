import {
  CheckCircleFilled, ClockCircleOutlined, CloseCircleFilled, ExclamationCircleFilled,
  FileTextOutlined, LoadingOutlined, MinusCircleOutlined, PauseCircleOutlined,
  SafetyCertificateOutlined, SyncOutlined,
} from '@ant-design/icons';
import type { ReactNode } from 'react';
import type { Tone } from '../app/theme';

export type BadgeKind = 'run' | 'task' | 'step';

type BadgeSpec = { label: string; tone: Tone; icon: ReactNode };

const RUN: Record<string, BadgeSpec> = {
  queued: { label: '排队中', tone: 'neutral', icon: <ClockCircleOutlined /> },
  running: { label: '运行中', tone: 'running', icon: <LoadingOutlined /> },
  waiting_approval: { label: '待交付审批', tone: 'waiting', icon: <SafetyCertificateOutlined /> },
  succeeded: { label: '已完成', tone: 'success', icon: <CheckCircleFilled /> },
  partial_failed: { label: '部分失败', tone: 'danger', icon: <ExclamationCircleFilled /> },
  failed: { label: '失败', tone: 'danger', icon: <CloseCircleFilled /> },
  paused: { label: '已暂停', tone: 'neutral', icon: <PauseCircleOutlined /> },
  pause_requested: { label: '暂停中', tone: 'neutral', icon: <SyncOutlined spin /> },
  cancelled: { label: '已停止', tone: 'neutral', icon: <MinusCircleOutlined /> },
  cancel_requested: { label: '停止中', tone: 'neutral', icon: <SyncOutlined spin /> },
};

const TASK: Record<string, BadgeSpec> = {
  UNVERIFIED: { label: '业务未验证', tone: 'waiting', icon: <ExclamationCircleFilled /> },
  SKIPPED: { label: '已跳过', tone: 'neutral', icon: <MinusCircleOutlined /> },
  RUNNING: { label: '运行中', tone: 'running', icon: <LoadingOutlined /> },
  RETRYING: { label: '重试中', tone: 'running', icon: <SyncOutlined spin /> },
  PAUSED: { label: '已暂停', tone: 'neutral', icon: <PauseCircleOutlined /> },
  CANCELLED: { label: '已停止', tone: 'neutral', icon: <MinusCircleOutlined /> },
  PARTIAL_FAILED: { label: '部分失败', tone: 'danger', icon: <ExclamationCircleFilled /> },
  PENDING: { label: '等待中', tone: 'neutral', icon: <ClockCircleOutlined /> },
  WAITING_APPROVAL: { label: '待交付审批', tone: 'waiting', icon: <SafetyCertificateOutlined /> },
  SUCCESS: { label: '待手动合并', tone: 'success', icon: <CheckCircleFilled /> },
  SUCCEEDED: { label: '待手动合并', tone: 'success', icon: <CheckCircleFilled /> },
  DELIVERED: { label: '交付已提交', tone: 'success', icon: <CheckCircleFilled /> },
  COMPLETED: { label: '已结束（无交付记录）', tone: 'neutral', icon: <MinusCircleOutlined /> },
  MANUAL: { label: '转人工报告', tone: 'manual', icon: <FileTextOutlined /> },
  INSUFFICIENT_EVIDENCE: { label: '证据不足', tone: 'manual', icon: <FileTextOutlined /> },
  REJECTED: { label: '已驳回', tone: 'danger', icon: <CloseCircleFilled /> },
  FAILED: { label: '失败', tone: 'danger', icon: <CloseCircleFilled /> },
  FAIL: { label: '失败', tone: 'danger', icon: <CloseCircleFilled /> },
};

const STEP: Record<string, BadgeSpec> = {
  pending: { label: '待执行', tone: 'neutral', icon: <ClockCircleOutlined /> },
  not_run: { label: '未执行', tone: 'neutral', icon: <MinusCircleOutlined /> },
  running: { label: '运行中', tone: 'running', icon: <LoadingOutlined /> },
  waiting: { label: '等待审批', tone: 'waiting', icon: <SafetyCertificateOutlined /> },
  succeeded: { label: '成功', tone: 'success', icon: <CheckCircleFilled /> },
  failed: { label: '失败', tone: 'danger', icon: <CloseCircleFilled /> },
  skipped: { label: '跳过', tone: 'neutral', icon: <MinusCircleOutlined /> },
};

function lookup(kind: BadgeKind, value?: string | null): BadgeSpec {
  const key = String(value ?? '').trim();
  const source = kind === 'run' ? RUN : kind === 'task' ? TASK : STEP;
  return source[key] ?? source[key.toUpperCase()] ?? { label: key || '未开始', tone: 'pending', icon: <ClockCircleOutlined /> };
}

export function toneOf(kind: BadgeKind, value?: string | null): Tone {
  return lookup(kind, value).tone;
}

export function StatusBadge({
  kind, value, compact = false,
}: { kind: BadgeKind; value?: string | null; compact?: boolean }) {
  const spec = lookup(kind, value);
  return (
    <span className={`status-badge tone-${spec.tone}${compact ? ' is-compact' : ''}`} title={spec.label}>
      <span className="status-badge-icon">{spec.icon}</span>
      <span className="status-badge-label">{spec.label}</span>
    </span>
  );
}
