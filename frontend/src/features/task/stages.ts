import { WORKFLOW_STAGES } from '../../lib/workflow.generated';
import type { StepRecord } from '../../lib/types';

export type TaskStage = {
  key: string;
  title: string;
  members: string[];
  group: boolean;
};

/** 与 app/workflow/definitions.py 的任务子图保持一致；组合节点展开为成员步骤。 */
export const TASK_STAGES: TaskStage[] = WORKFLOW_STAGES;

export const AGENT_STEPS = new Set(['locate_agent', 'repair_agent', 'review_agent']);

/** 执行成功但业务未通过的结果；用于徽标和默认选中步骤。 */
// MANUAL / INSUFFICIENT_EVIDENCE 是人工处理结论，不是执行失败；不要把它们
// 当成失败步骤自动选中或在任务详情中与真实 FAIL 混为一类。
export const FAILED_RESULTS = new Set([
  'FAIL', 'FAILED', 'REJECTED',
]);

export function stepRecord(steps: StepRecord[], name: string): StepRecord | undefined {
  return steps.find((step) => step.name === name);
}

/** 默认选中的步骤：优先业务失败/执行失败/运行中/等待，否则最后一个已记录步骤。 */
export function defaultStep(steps: StepRecord[]): string {
  // finalize_execution 的 FAILED 只是终态报告结论，不是失败源头；优先选真实失败步骤。
  const businessFailed = steps.find(
    (step) => step.name !== 'finalize_execution'
      && (step.assessment?.outcome === 'failed' || FAILED_RESULTS.has(String(step.result || '').toUpperCase())),
  );
  if (businessFailed) return businessFailed.name;
  const order = ['failed', 'running', 'waiting'] as const;
  for (const status of order) {
    const found = [...steps].reverse().find((step) => step.status === status);
    if (found) return found.name;
  }
  return steps[steps.length - 1]?.name ?? TASK_STAGES[0].members[0];
}
