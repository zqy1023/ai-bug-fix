import type { StepRecord } from './types';
import { text } from './taskResults';

// Historical records may mark a caught SDK failure as a completed node.
// Preserve that execution record while showing the actual agent outcome.
export function stepPresentation(step: StepRecord) {
  const invocationFailed = ['agent_invocation_failed', 'agent_startup_failed'].includes(text(step.output?.failure_kind));
  const executionFailed = step.execution?.status === 'failed' || step.status === 'failed' || invocationFailed;
  const assessment = step.assessment?.outcome;
  const rejected = assessment === 'failed' || ['FAIL', 'FAILED', 'REJECTED'].includes(step.result || '');
  if (executionFailed) return { tone: 'bad', label: invocationFailed ? '模型调用失败' : '执行失败' };
  if (['running', 'waiting'].includes(step.status)) return { tone: 'active', label: step.status === 'running' ? '执行中' : '等待' };
  if (step.status === 'pending') return { tone: 'idle', label: '未开始' };
  if (step.status === 'skipped') return { tone: 'warn', label: '跳过' };
  if (rejected) return { tone: 'bad', label: '结论未通过' };
  if (assessment === 'inconclusive') return { tone: 'warn', label: '未能确认' };
  return { tone: 'ok', label: '完成' };
}
