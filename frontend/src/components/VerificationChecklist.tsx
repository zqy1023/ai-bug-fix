import { Button } from 'antd';
import { CheckCircleOutlined, ClockCircleOutlined, ExclamationCircleOutlined } from '@ant-design/icons';
import type { StepRecord } from '../lib/types';
import styles from './VerificationChecklist.module.css';

interface VerificationChecklistProps {
  readonly steps: StepRecord[];
  readonly layout?: 'list' | 'ribbon';
  readonly onOpenStep: (name: string) => void;
}
const CHECKS = [
  { key: 'syntax', title: '语法检查' },
  { key: 'business', title: '业务修复检查' },
  { key: 'scope', title: '影响范围检查' },
];
type Dimension = { status?: string; reason?: string };

export function VerificationChecklist({ steps, onOpenStep, layout = 'list' }: VerificationChecklistProps) {
  const record = [...steps].reverse().find((step) => step.name === 'review_agent');
  const payload = (record?.output?.payload || record?.output || {}) as Record<string, unknown>;
  const repair = [...steps].reverse().find((step) => step.name === 'repair_agent');
  const stale = Boolean(payload.stale || (record && repair && Date.parse(repair.started_at || '') > Date.parse(record.started_at || '')));
  const current = Number(payload.review_contract_version) >= 6 && !payload.contract_error;
  const dimensions = (current ? payload.dimensions || {} : {}) as Record<string, Dimension>;
  const verdict = stale ? '' : current ? String(payload.verdict || '') : '';
  const label = stale ? '证据已过期' : verdict === 'PASS' ? '通过' : verdict === 'FAIL' ? '需要修改' : verdict === 'MANUAL' ? '证据不足' : record?.status === 'running' ? '审查中' : '待审查';
  return <section aria-label="修复后代码审查 Agent">
    <div className={styles.row}>
      <div className={styles.body}><strong>修复后代码审查 Agent</strong><p>{stale ? '代码或输入已变化，需要重新审查。' : current ? String(payload.summary || '按三个维度综合判断修复效果。') : '按语法、业务修复和影响范围分步审查，综合判断修复是否成功。'}</p></div>
      <span className={styles.badge}>{label}</span>
      {record && <Button size="small" type="link" onClick={() => onOpenStep('review_agent')}>查看审查证据 →</Button>}
    </div>
    <ul className={`${styles.list}${layout === 'ribbon' ? ` ${styles.ribbon}` : ''}`} aria-label="代码审查三个维度">
      {CHECKS.map((check) => {
        const item = dimensions[check.key];
        const status = stale ? 'unverified' : item?.status;
        const passed = status === 'passed';
        const failed = status === 'failed';
        const color = passed ? styles.passed : failed ? styles.failed : styles.unknown;
        return <li key={check.key} className={styles.row}>
          <span className={color}>{passed ? <CheckCircleOutlined /> : failed ? <ExclamationCircleOutlined /> : <ClockCircleOutlined />}</span>
          <div className={styles.body}><strong>{check.title}</strong><p>{stale ? '证据已过期，需要重新审查。' : item?.reason || '等待 Agent 核对源码与执行证据。'}</p></div>
          <span className={`${styles.badge} ${color}`}>{passed ? '通过' : failed ? '需要修改' : '待确认'}</span>
        </li>;
      })}
    </ul>
  </section>;
}
