import type { StepRecord, StepStatus } from '../../lib/types';

export type TaskStage = {
  key: string;
  title: string;
  members: string[];
  group: boolean;
};

/** 与 app/workflow/definitions.py 的任务子图保持一致；组合节点展开为成员步骤。 */
export const TASK_STAGES: TaskStage[] = [
  { key: 'prepare_context', title: '准备上下文', members: ['prepare_source_snapshot', 'repair_branch_preflight', 'prepare_workspace', 'prepare_evidence'], group: true },
  { key: 'locate_agent', title: '定位问题', members: ['locate_agent'], group: false },
  { key: 'claim_repair_slot', title: '领取修复资格', members: ['claim_repair_slot'], group: false },
  { key: 'repair_agent', title: '修复 Agent', members: ['repair_agent'], group: false },
  { key: 'validate_changes', title: '变更门禁', members: ['check_changes', 'verify_issue'], group: true },
  { key: 'run_regression', title: '回归验证', members: ['run_regression'], group: false },
  { key: 'review_agent', title: 'AI 审查', members: ['review_agent'], group: false },
  { key: 'prepare_delivery', title: '交付修复分支', members: ['prepare_delivery'], group: false },
  { key: 'request_approval', title: '交付审批', members: ['request_approval'], group: false },
  { key: 'finalize_execution', title: '终局报告', members: ['finalize_execution'], group: false },
];

export const AGENT_STEPS = new Set(['locate_agent', 'repair_agent', 'review_agent']);

/** 执行成功但业务未通过的结果；用于徽标和默认选中步骤。 */
// MANUAL / INSUFFICIENT_EVIDENCE 是人工处理结论，不是执行失败；不要把它们
// 当成失败步骤自动选中或在任务详情中与真实 FAIL 混为一类。
export const FAILED_RESULTS = new Set([
  'FAIL', 'FAILED', 'REJECTED',
]);

/** 会产生外部写入、需要显式确认的步骤（服务端仍是最终权威）。 */
export const WRITE_STEPS = new Set([
  'prepare_workspace', 'repair_branch_preflight', 'claim_repair_slot',
  'locate_agent', 'repair_agent', 'prepare_delivery',
]);

const RANK: Record<StepStatus, number> = {
  failed: 6, running: 5, waiting: 4, succeeded: 3, skipped: 2, pending: 1,
};

export function stepRecord(steps: StepRecord[], name: string): StepRecord | undefined {
  return steps.find((step) => step.name === name);
}

export function stageStatus(stage: TaskStage, steps: StepRecord[]): StepStatus {
  const records = stage.members.map((name) => stepRecord(steps, name)).filter(Boolean) as StepRecord[];
  if (!records.length) return 'pending';
  return records.reduce<StepStatus>(
    (worst, record) => (RANK[record.status] > RANK[worst] ? record.status : worst),
    'pending',
  );
}

export function stageProgress(stage: TaskStage, steps: StepRecord[]): { done: number; total: number } {
  const records = stage.members.map((name) => stepRecord(steps, name)).filter(Boolean) as StepRecord[];
  return {
    done: records.filter((record) => record.status === 'succeeded').length,
    total: stage.members.length,
  };
}

/** 默认选中的步骤：优先业务失败/执行失败/运行中/等待，否则最后一个已记录步骤。 */
export function defaultStep(steps: StepRecord[]): string {
  // finalize_execution 的 FAILED 只是终态报告结论，不是失败源头；优先选真实失败步骤。
  const businessFailed = steps.find(
    (step) => step.name !== 'finalize_execution'
      && FAILED_RESULTS.has(String(step.result || '').toUpperCase()),
  );
  if (businessFailed) return businessFailed.name;
  const order = ['failed', 'running', 'waiting'] as const;
  for (const status of order) {
    const found = [...steps].reverse().find((step) => step.status === status);
    if (found) return found.name;
  }
  return steps[steps.length - 1]?.name ?? TASK_STAGES[0].members[0];
}

export function stageForStep(stepName: string): TaskStage | undefined {
  return TASK_STAGES.find((stage) => stage.members.includes(stepName));
}

export function primaryStep(stage: TaskStage, steps: StepRecord[]): string {
  const recorded = stage.members.filter((name) => stepRecord(steps, name));
  return recorded[recorded.length - 1] ?? stage.members[0];
}
