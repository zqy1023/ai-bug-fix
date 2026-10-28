import { WORKFLOW_STAGES } from '../../lib/workflow.generated';
import type { StepRecord } from '../../lib/types';

export type TaskStage = {
  key: string;
  title: string;
  members: string[];
  group: boolean;
};

/** 与 workflow/engine/handoff.py 的任务子图阶段保持一致；组合节点展开为成员步骤。 */
export const TASK_STAGES: TaskStage[] = WORKFLOW_STAGES;

/** 只有终态收尾记录不属于公开的业务阶段。 */
const INTERNAL_STEP_LABELS: Record<string, string> = {
  finalize_execution: '收尾汇总',
};

/** 任务级步骤名以 WORKFLOW_STAGES 为准，避免前后端各维护一份而漂移。 */
function deriveStepLabels(): Record<string, string> {
  const derived: Record<string, string> = {};
  for (const stage of WORKFLOW_STAGES) {
    derived[stage.key] = stage.title;
    for (const member of stage.members) derived[member] = stage.title;
  }
  return { ...derived, ...INTERNAL_STEP_LABELS };
}

export const STEP_LABELS: Record<string, string> = deriveStepLabels();

export const AGENT_STEPS = new Set(['locate_agent', 'repair_agent', 'review_agent', 'unified_fixer_agent']);

export type VisibleStage = {
  stage: TaskStage;
  records: StepRecord[];
  representative?: StepRecord;
};

/** 将内部原子记录聚合到公开业务阶段，主页面不再暴露实现细节。 */
export function visibleStages(steps: StepRecord[], stages: TaskStage[] = TASK_STAGES): VisibleStage[] {
  const byName = new Map(steps.map((step) => [step.name, step]));
  return stages.map((stage) => {
    const records = stage.members
      .map((name) => byName.get(name))
      .filter((record): record is StepRecord => Boolean(record));
    const representative = records.find((record) => record.status === 'failed' || record.status === 'waiting' || record.status === 'running')
      || records.at(-1);
    return { stage, records, representative };
  });
}

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
