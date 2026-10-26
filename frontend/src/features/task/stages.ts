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

/**
 * WORKFLOW_STAGES 之外的显式补充表。
 * 这里只保留真源里没有独立标题的条目：run 级步骤、审查 Agent 内部工具，
 * 以及 prepare_context 组合阶段内部的四个成员步骤。
 */
const EXTRA_STEP_LABELS: Record<string, string> = {
  // run 级步骤：不属于任务子图，WORKFLOW_STAGES 不包含。
  discover_logs: '拉取异常日志',
  normalize_error: '规范化异常',
  deduplicate_error: '异常去重',
  identify_carrier: '识别承运商',
  // 审查 Agent 内部工具：只在审查产物里出现，不是任务子图节点。
  check_changes: '变更范围核验',
  syntax_regression: '语法回归检查',
  plan_validation: '验证计划',
  verify_issue: '原问题重放验证',
  run_regression: '真实请求回归',
  summarize_validation: '验证结论汇总',
  // prepare_context 是组合阶段，成员在真源里没有单独标题，保留人工命名。
  repair_branch_preflight: '检查已有分支',
  prepare_source_snapshot: '源码快照',
  prepare_workspace: '准备隔离工作区',
  prepare_evidence: '准备定位证据',
};

/** 任务级步骤名以 WORKFLOW_STAGES 为准，避免前后端各维护一份而漂移。 */
function deriveStepLabels(): Record<string, string> {
  const derived: Record<string, string> = {};
  for (const stage of WORKFLOW_STAGES) {
    derived[stage.key] = stage.title;
    // 组合阶段（group=true）只有阶段标题，成员名称由补充表提供。
    if (!stage.group) for (const member of stage.members) derived[member] = stage.title;
  }
  return { ...derived, ...EXTRA_STEP_LABELS };
}

export const STEP_LABELS: Record<string, string> = deriveStepLabels();

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
