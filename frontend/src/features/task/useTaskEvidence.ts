import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import type { StepRecord, TaskDetailResponse } from '../../lib/types';
import {
  REVIEW_TOOL_NAMES, evidenceState, reviewSubsteps, substepToRecord, toStepRecord,
  type EvidenceState, type ReviewSubstep, type ReviewToolName,
} from './reviewEvidence';
import { latestStep, type EvidenceResolution } from './taskVerdict';

/**
 * 审查 Agent 的内部工具通常不在步骤主表里：
 * 先从 review_agent 输出读出内部工具清单，再按需取回归档的正式记录；
 * 取不到时明确区分“未执行”与“数据未回传”，不推断结论。
 */
export function useTaskEvidence(detail?: TaskDetailResponse | null): EvidenceResolution {
  const steps = detail?.task?.steps ?? [];
  const reviewStep = latestStep(steps, 'review_agent');
  const runId = detail?.run_id ?? '';
  const taskId = detail?.task?.task_id ?? '';

  const substeps = useMemo(() => reviewSubsteps(reviewStep?.output), [reviewStep?.output]);
  const substepByName = useMemo(
    () => new Map(substeps.map((item) => [item.name, item] as const)),
    [substeps],
  );
  const missingToolPaths = useMemo(() => REVIEW_TOOL_NAMES.flatMap((name) => {
    if (latestStep(steps, name)) return [];
    const path = substepByName.get(name)?.recordPath ?? '';
    return path ? [{ name, path }] : [];
  }), [steps, substepByName]);

  const internal = useQuery({
    queryKey: [...queryKeys.artifactContent(runId, '', taskId), { internal: missingToolPaths }],
    queryFn: async () => {
      const entries = await Promise.all(missingToolPaths.map(async ({ name, path }) => {
        try {
          const response = await api.artifactContent(runId, path, taskId);
          return [name, toStepRecord(response.content)] as const;
        } catch {
          // 归档缺失时保持空值，由证据区显示“未回传”，不伪造结果。
          return [name, undefined] as const;
        }
      }));
      return new Map(entries);
    },
    enabled: Boolean(runId && taskId && missingToolPaths.length > 0),
    staleTime: 60_000,
  });

  // 取值顺序：步骤主表 → 归档的完整记录 → 审查输出里的内部工具摘要。
  const toolStep = (name: ReviewToolName): StepRecord | undefined =>
    latestStep(steps, name) ?? internal.data?.get(name) ?? substepToRecord(substepByName.get(name));
  const toolSummary = (name: ReviewToolName): string => {
    const found: ReviewSubstep | undefined = substepByName.get(name);
    return found?.summary || '';
  };

  const reviewRan = Boolean(reviewStep);
  const checkStep = toolStep('check_changes');
  const verifyStep = toolStep('verify_issue');
  const regressionStep = toolStep('run_regression');
  const syntaxStep = toolStep('syntax_regression');
  const state = (step: StepRecord | undefined, name: ReviewToolName): EvidenceState =>
    evidenceState(step, substepByName.get(name), reviewRan);

  return {
    checkStep,
    verifyStep,
    regressionStep,
    syntaxStep,
    reviewStep,
    checkState: state(checkStep, 'check_changes'),
    verifyState: state(verifyStep, 'verify_issue'),
    regressionState: state(regressionStep, 'run_regression'),
    syntaxState: state(syntaxStep, 'syntax_regression'),
    reviewRan,
    substepSummary: (name: string) => toolSummary(name as ReviewToolName),
  };
}
