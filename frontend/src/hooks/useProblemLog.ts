import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { TaskDetailResponse } from '../lib/types';
import { api } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import { dict, text } from '../features/task/taskVerdict';

function originalMessage(value: unknown): string {
  return typeof value === 'string' && value.trim() ? value : '';
}

export interface ProblemLog {
  /** 完整原始日志 message；为空表示确实没有记录。 */
  message: string;
  /** 从日志中提取出的 error 字段，不能替代完整 message。 */
  extracted: string;
  /** 完整日志是否取自 run 级去重产物（历史任务步骤记录曾被裁剪）。 */
  fromArchive: boolean;
  /** 是否正在加载产物中的完整原始日志 */
  loading?: boolean;
  failed?: boolean;
}

/**
 * 原始日志读取：只读取任务记录里的完整 message，
 * 历史任务回读 run 级 normalized_records 产物；提取出的 error 单独标注，不冒充原始 message。
 */
export function useProblemLog(
  detail?: TaskDetailResponse,
  initialProblem?: Record<string, unknown>,
  runIdProp?: string,
): ProblemLog {
  const problem = dict(detail?.task.problem ?? initialProblem);
  const runId = detail?.run_id ?? runIdProp ?? '';
  const needsArchive = Boolean(runId)
    && !originalMessage(problem.message) && Boolean(text(problem.error) || text(problem.carrier));

  const query = useQuery({
    queryKey: [...queryKeys.artifactContent(runId, 'artifacts/normalized_records.json'), 'problem-log'],
    queryFn: () => api.artifactContent(runId, 'artifacts/normalized_records.json'),
    enabled: needsArchive,
    staleTime: 5 * 60_000,
  });

  return useMemo(() => {
    const direct = originalMessage(problem.message);
    if (direct) {
      return { message: direct, extracted: text(problem.error), fromArchive: false, loading: false };
    }
    if (!needsArchive || !query.data?.content) {
      return { message: '', extracted: text(problem.error), fromArchive: false, loading: needsArchive && query.isLoading, failed: needsArchive && query.isError };
    }
    const rawRecords = Array.isArray(dict(query.data.content).records)
      ? (dict(query.data.content).records as Array<Record<string, unknown>>)
      : [];
    const records = rawRecords.map(dict);
    // 按 (carrier, biztype, error) 匹配代表问题
    const hit = records.find((record) => {
      const item = dict(record.problem);
      const matchCarrier = text(item.carrier) === text(problem.carrier);
      const matchBiz = text(item.biztype) === text(problem.biztype);
      if (!matchCarrier || !matchBiz) return false;
      if (text(problem.service_name) && text(item.service_name) && text(item.service_name) !== text(problem.service_name)) {
        return false;
      }
      if (text(problem.error) && text(item.error) && text(item.error) !== text(problem.error)) {
        return false;
      }
      return true;
    });
    const archived = dict(hit?.problem);
    const msg = originalMessage(archived.message) || originalMessage(hit?.message);
    return {
      message: msg,
      extracted: text(problem.error) || text(problem.raw_error) || text(archived.error) || text(hit?.error),
      fromArchive: Boolean(msg),
      loading: false,
    };
  }, [needsArchive, problem, query.data, query.isLoading, query.isError]);
}
