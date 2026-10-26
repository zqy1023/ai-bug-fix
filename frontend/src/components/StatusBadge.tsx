import type { CSSProperties, ReactNode } from 'react';
import { toneVar, type Tone } from '../app/theme';
import styles from './StatusBadge.module.css';

export type BadgeKind = 'run' | 'task' | 'step';

type BadgeSpec = { label: string; tone: Tone; live: boolean };

const RUN: Record<string, BadgeSpec> = {
  queued: { label: '排队中', tone: 'neutral', live: false },
  running: { label: '运行中', tone: 'running', live: true },
  waiting_approval: { label: '待交付审批', tone: 'waiting', live: false },
  succeeded: { label: '已完成', tone: 'success', live: false },
  partial_failed: { label: '部分失败', tone: 'danger', live: false },
  failed: { label: '失败', tone: 'danger', live: false },
  paused: { label: '已暂停', tone: 'neutral', live: false },
  pause_requested: { label: '暂停中', tone: 'neutral', live: true },
  cancelled: { label: '已停止', tone: 'neutral', live: false },
  cancel_requested: { label: '停止中', tone: 'neutral', live: true },
};

const TASK: Record<string, BadgeSpec> = {
  UNVERIFIED: { label: '业务未验证', tone: 'waiting', live: false },
  SKIPPED: { label: '已跳过', tone: 'neutral', live: false },
  RUNNING: { label: '运行中', tone: 'running', live: true },
  RETRYING: { label: '重试中', tone: 'running', live: true },
  PAUSED: { label: '已暂停', tone: 'neutral', live: false },
  CANCELLED: { label: '已停止', tone: 'neutral', live: false },
  PARTIAL_FAILED: { label: '部分失败', tone: 'danger', live: false },
  PENDING: { label: '等待中', tone: 'neutral', live: false },
  WAITING_APPROVAL: { label: '待交付审批', tone: 'waiting', live: false },
  SUCCESS: { label: '待手动合并', tone: 'success', live: false },
  SUCCEEDED: { label: '待手动合并', tone: 'success', live: false },
  DELIVERED: { label: '交付已提交', tone: 'success', live: false },
  COMPLETED: { label: '已结束（无交付记录）', tone: 'neutral', live: false },
  BLOCKED: { label: '验证受阻', tone: 'waiting', live: false },
  MANUAL: { label: '需人工处理', tone: 'manual', live: false },
  INSUFFICIENT_EVIDENCE: { label: '证据不足', tone: 'manual', live: false },
  REJECTED: { label: '已驳回', tone: 'danger', live: false },
  FAILED: { label: '失败', tone: 'danger', live: false },
  FAIL: { label: '失败', tone: 'danger', live: false },
};

const STEP: Record<string, BadgeSpec> = {
  pending: { label: '待执行', tone: 'neutral', live: false },
  not_run: { label: '未执行', tone: 'neutral', live: false },
  running: { label: '运行中', tone: 'running', live: true },
  waiting: { label: '已暂停', tone: 'neutral', live: false },
  waiting_approval: { label: '等待审批', tone: 'waiting', live: false },
  succeeded: { label: '成功', tone: 'success', live: false },
  failed: { label: '失败', tone: 'danger', live: false },
  skipped: { label: '跳过', tone: 'neutral', live: false },
};

function lookup(kind: BadgeKind, value?: string | null, stepName?: string): BadgeSpec {
  const raw = String(value ?? '').trim();
  const key = kind === 'step' && raw === 'waiting' && stepName === 'request_approval'
    ? 'waiting_approval' : raw;
  const source = kind === 'run' ? RUN : kind === 'task' ? TASK : STEP;
  return source[key] ?? source[key.toUpperCase()] ?? { label: key || '未开始', tone: 'pending', live: false };
}

export function toneOf(kind: BadgeKind, value?: string | null): Tone {
  return lookup(kind, value).tone;
}

/** 状态原语:圆点(语义色 + 光环)+ 文字;文字保持正文色,不整块着色。 */
export function StatusBadge({
  kind, value, stepName, compact = false,
}: { kind: BadgeKind; value?: string | null; stepName?: string; compact?: boolean }) {
  const spec = lookup(kind, value, stepName);
  const toneStyle = { '--tone': `var(--wb-${toneVar[spec.tone]})` } as CSSProperties;
  return (
    <span className={`${styles.badge}${compact ? ` ${styles.compact}` : ''}`} style={toneStyle} title={spec.label}>
      <span className={`${styles.dot}${spec.live ? ` ${styles.live}` : ''}`} aria-hidden="true" />
      <span>{spec.label}</span>
    </span>
  );
}

/** 任意文案 + 语义圆点;用于非枚举状态的轻量标记。 */
export function StatusDot({ tone, label, live = false, children }: { tone: Tone; label?: string; live?: boolean; children: ReactNode }) {
  const toneStyle = { '--tone': `var(--wb-${toneVar[tone]})` } as CSSProperties;
  return (
    <span className={styles.badge} title={label}>
      <span className={`${styles.dot}${live ? ` ${styles.live}` : ''}`} style={toneStyle} aria-hidden="true" />
      <span>{children}</span>
    </span>
  );
}
