import type { StepRecord } from '../../lib/types';
import { StatusDot } from '../../components/StatusBadge';
import type { Tone } from '../../app/theme';
import styles from './StepAssessment.module.css';

const executionLabels: Record<string, string> = {
  succeeded: '执行完成', failed: '执行失败', waiting: '等待输入', cancelled: '已取消',
  pending: '尚未开始', running: '正在执行', skipped: '已跳过',
};
const assessmentLabels: Record<string, string> = {
  passed: '业务通过', failed: '业务未通过', inconclusive: '证据不足，尚无定论',
  not_applicable: '本步骤不判定业务结果',
};

function executionTone(status: string): Tone {
  if (status === 'failed') return 'danger';
  if (status === 'running') return 'running';
  if (status === 'waiting') return 'waiting';
  if (status === 'succeeded') return 'success';
  return 'neutral';
}

function assessmentTone(outcome?: string): Tone {
  if (outcome === 'passed') return 'success';
  if (outcome === 'failed') return 'danger';
  if (outcome === 'inconclusive') return 'manual';
  return 'neutral';
}

/** 只解释正式契约；旧记录没有 assessment 时明确展示证据缺失。 */
export function StepAssessment({ step }: { step: StepRecord }) {
  const execution = step.execution?.status ?? step.status;
  const assessment = step.assessment;
  return <section className={styles.verdicts} aria-label="步骤判定">
    <div className={styles.bar}>
      <div className={styles.k}><span>执行状态</span><code>execution</code></div>
      <div className={styles.v}>
        <StatusDot tone={executionTone(execution)}>{executionLabels[execution] || execution}</StatusDot>
      </div>
      <p className={styles.d}>{step.execution?.error?.message || step.error?.message || '程序执行状态与业务验收分别记录'}</p>
    </div>
    <div className={styles.bar}>
      <div className={styles.k}><span>业务判定</span><code>assessment</code></div>
      <div className={styles.v}>
        <StatusDot tone={assessmentTone(assessment?.outcome)}>
          {assessment ? assessmentLabels[assessment.outcome] : '尚无正式判定'}
        </StatusDot>
        {assessment?.code && <code className={styles.code}>{assessment.code}</code>}
      </div>
      <p className={styles.d}>{assessment?.summary || (step.result ? `历史记录：${step.result}，未提供正式验收结论` : '等待步骤产出正式评估')}</p>
    </div>
    {Boolean(assessment?.findings?.length) && (
      <ul className={styles.findings}>
        {assessment!.findings!.map((finding, index) => <li key={index}>{finding}</li>)}
      </ul>
    )}
  </section>;
}
