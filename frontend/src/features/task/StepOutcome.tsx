import type { StepRecord } from '../../lib/types';

type Dict = Record<string, unknown>;
const object = (value: unknown): Dict => value && typeof value === 'object' && !Array.isArray(value) ? value as Dict : {};
const text = (value: unknown): string => typeof value === 'string' ? value.trim() : '';
const labels: Record<string, string> = {
  root_cause: '根因', summary: '摘要', reason: '原因', terminal_reason: '停止原因',
  next_action: '下一步', verdict: '审查结论', status: '验证状态',
  repair_branch: '交付分支', commit: 'Commit', delivery_status: '交付状态',
};
export function outcomeText(step: StepRecord): string {
  const output = object(step.output);
  return text(step.error?.message) || text(output.root_cause) || text(output.summary) || text(output.reason)
    || text(output.terminal_reason) || text(step.summary) || '该节点已记录执行状态。';
}
export function StepOutcome({ step }: { step: StepRecord }) {
  const output = object(step.output);
  const fields = Object.entries(output).filter(([key, value]) => key in labels && value !== '' && value !== null && value !== undefined);
  return <div className="step-outcome step-outcome-compact">
    <p>{outcomeText(step)}</p>
    {fields.length > 0 && <dl className="step-outcome-fields">{fields.slice(0, 6).map(([key, value]) => <div key={key}><dt>{labels[key]}</dt><dd>{String(value)}</dd></div>)}</dl>}
    {step.error && <p className="step-error-message"><strong>{step.error.code || 'STEP_EXECUTION_FAILED'}</strong> · {text(step.error.message)}</p>}
  </div>;
}
