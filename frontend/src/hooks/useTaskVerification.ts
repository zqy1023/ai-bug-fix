import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import { stepStopKind, type StepStopKind } from '../lib/stepPresentation';
import type { TaskDetailResponse } from '../lib/types';

const object = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};

/** The archived report contains complete replay evidence; step output is a summary. */
export function useTaskVerification(detail: TaskDetailResponse) {
  const step = [...detail.task.steps].reverse().find((item) => item.name === 'review_agent');
  const artifact = (step?.artifacts || []).concat(detail.artifacts).find((item) => item.name === 'verification_report' || /[/\\]verification_report\.json$/.test(item.path));
  const output = object(step?.output?.payload || step?.output);
  const query = useQuery({
    queryKey: [...queryKeys.artifactContent(detail.run_id, artifact?.path || '', detail.task.task_id), { finishedAt: step?.finished_at, attempt: step?.attempt, evidenceVersion: output.evidence_version }],
    queryFn: () => api.artifactContent(detail.run_id, artifact!.path, detail.task.task_id),
    enabled: Boolean(artifact), staleTime: 60_000,
  });
  const report = { ...output, ...object(query.data?.content) };
  const replay = { ...object(report.replay), ...report };
  // 审查停点决定重放证据为什么可能不存在；取消、跳过与人工处理都不是失败结论。
  const stopKind: StepStopKind = stepStopKind(step);
  return { step, replay, isLoading: query.isLoading, error: query.error, hasComparison: Boolean(replay.replay_comparison), stopKind };
}
