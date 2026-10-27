import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { TaskDetailResponse } from '../lib/types';
import { api } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import { dict, text } from '../features/task/taskVerdict';

export interface ProblemLog {
  /** 完整原始日志 message；为空表示确实没有记录。 */
  message: string;
  /** 从日志中提取出的 error 字段，不能替代完整 message。 */
  extracted: string;
  /** 完整日志是否取自 run 级去重产物（历史任务步骤记录曾被裁剪）。 */
  fromArchive: boolean;
}

/**
 * 原始日志读取：优先任务记录里的 message / raw_error，
 * 历史任务回读 run 级 normalized_records 产物；提取出的 error 只作最后兜底。
 */
export function useProblemLog(detail?: TaskDetailResponse): ProblemLog {
  const problem = dict(detail?.task.problem);
  const runId = detail?.run_id ?? '';
  const taskId = detail?.task.task_id ?? '';
  const needsArchive = Boolean(detail)
    && !text(problem.message) && !text(problem.raw_error) && Boolean(text(problem.error));

  const query = useQuery({
    queryKey: [...queryKeys.artifactContent(runId, 'artifacts/normalized_records.json', taskId), 'problem-log'],
    queryFn: () => api.artifactContent(runId, 'artifacts/normalized_records.json', taskId),
    enabled: needsArchive,
    staleTime: 5 * 60_000,
  });

  return useMemo(() => {
    const direct = text(problem.message) || text(problem.raw_error);
    if (direct) return { message: direct, extracted: text(problem.error), fromArchive: false };
    if (!needsArchive || !query.data?.content) {
      return { message: '', extracted: text(problem.error), fromArchive: false };
    }
    const rawRecords = Array.isArray(dict(query.data.content).records)
      ? (dict(query.data.content).records as Array<Record<string, unknown>>)
      : [];
    const records = rawRecords.map(dict);
    // 按 (service_name, biztype, carrier, error) 匹配同一条代表问题，避免误取其他任务。
    const hit = records.find((record) => {
      const item = dict(record.problem);
      return text(item.service_name) === text(problem.service_name)
        && text(item.biztype) === text(problem.biztype)
        && text(item.carrier) === text(problem.carrier)
        && text(item.error) === text(problem.error);
    });
    const archived = dict(hit?.problem);
    return {
      message: text(archived.message) || text(archived.raw_error),
      extracted: text(problem.error),
      fromArchive: Boolean(text(archived.message) || text(archived.raw_error)),
    };
  }, [needsArchive, problem, query.data]);
}
