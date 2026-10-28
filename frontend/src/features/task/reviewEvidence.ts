import type { StepRecord } from '../../lib/types';
import { WORKFLOW_STAGES } from '../../lib/workflow.generated';

const WORKFLOW_NAMES = new Set(WORKFLOW_STAGES.flatMap(stage => stage.members));
/** 只展示当前工作流节点；历史产物通过通用归档入口读取。 */
export function workflowSteps(steps: StepRecord[]): StepRecord[] {
  return steps.filter(step => WORKFLOW_NAMES.has(step.name) || step.name === 'finalize_execution');
}
export type EvidenceState = 'available' | 'not_returned' | 'not_executed';
export const EVIDENCE_STATE_LABELS = { available: '已回传', not_returned: '未记录', not_executed: '未执行' };
