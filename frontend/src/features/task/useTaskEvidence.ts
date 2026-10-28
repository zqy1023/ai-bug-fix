import type { TaskDetailResponse } from '../../lib/types';
import { latestStep, type EvidenceResolution } from './taskVerdict';

/** 审查证据直接读取 Agent 输出，不再请求旧检查步骤归档。 */
export function useTaskEvidence(detail?: TaskDetailResponse | null): EvidenceResolution {
  return { reviewStep: latestStep(detail?.task.steps ?? [], 'review_agent') };
}
