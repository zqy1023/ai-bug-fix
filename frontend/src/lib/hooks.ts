import { useEffect, useMemo, useState, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './api';
import { subscribeRunEvents, type StreamState } from './events';
import type { ReviewPayload, TaskDetailResponse } from './types';
import { reviewOf } from './taskReview';


export function useRunEvents(runId?: string, enabled = true) {
  const queryClient = useQueryClient();
  const [streamState, setStreamState] = useState<StreamState>('connecting');
  const stream = useRef<StreamState>('connecting');
  const run = useQuery({
    queryKey: ['run', runId], queryFn: () => api.run(runId as string), enabled: Boolean(runId && enabled),
    // 终态后批次不再变化：SSE 会主动断开（服务端终态关闭流），此处的轮询只是
    // 兜底，继续跑只会一直打 API 而拿不到新数据。
    refetchInterval: (query) => query.state.data?.terminal ? false : 5_000,
  });
  const terminal = run.data?.terminal === true;
  useEffect(() => {
    if (!runId || !enabled || terminal) return undefined;
    const refresh = (lists = false, liveCallsOnly = false) => {
      const keys = liveCallsOnly ? ['model-calls', 'agent-call'] : ['board', 'task', 'step', 'model-calls', 'agent-call', 'artifact-content', 'task-changes', 'checkpoints', 'control-audit'];
      for (const key of keys) {
        void queryClient.invalidateQueries({ queryKey: [key, runId] }, { cancelRefetch: false });
      }
      if (lists) {
        void queryClient.invalidateQueries({ queryKey: ['runs'] }, { cancelRefetch: false });
        void queryClient.invalidateQueries({ queryKey: ['dashboard'] }, { cancelRefetch: false });
      }
    };
    let previousRevision = '';
    const stop = subscribeRunEvents(runId, {
      onState: state => {stream.current = state; setStreamState(state);},
      onSnapshot: (snapshot) => {
        queryClient.setQueryData(['run', runId], snapshot);
        const revision = JSON.stringify([snapshot.status, snapshot.completed_tasks,
          snapshot.tasks.map(task => [task.task_id, task.result, task.current_step, task.attempt, task.steps_count])]);
        if (revision !== previousRevision) {refresh(true); previousRevision = revision;}
      },
    });
    // Agent 对话追加不会总改变批次快照；轮询兜底覆盖流断线及节点长时间执行。
    // terminal 时上面的 effect 已提前返回（不订阅、不建定时器），终态批次不再变化。
    const timer = window.setInterval(() => {
      if (document.visibilityState !== 'hidden') refresh(false, stream.current === 'open');
    }, 10_000);
    return () => { stop(); window.clearInterval(timer); };
  }, [enabled, queryClient, runId, terminal]);
  return { streamState, terminal, updatedAt: run.dataUpdatedAt, error: run.error };
}

export function useLiveClock(enabled: boolean): number {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!enabled) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [enabled]);
  return now;
}


export interface ProblemLog {
  message: string;
  relatedMessages: string[];
  extracted: string;
  truncated: boolean;
  loading: boolean;
  failed: boolean;
  fromArchive: boolean;
}

/** 原始异常由后端无损归档接口提供，不按 carrier/biztype 猜另一条日志。 */
export function useProblemLog(detail?: TaskDetailResponse): ProblemLog {
  const runId = detail?.run_id ?? '';
  const taskId = detail?.task.task_id ?? '';
  const query = useQuery({
    queryKey: ['problem-log', runId, taskId],
    queryFn: () => api.problemLog(runId, taskId),
    enabled: Boolean(runId && taskId), staleTime: 5 * 60_000,
  });
  return {
    message: query.data?.message ?? '',
    relatedMessages: query.data?.related_messages ?? [],
    extracted: String(detail?.task.problem?.error ?? ''),
    truncated: query.data?.truncated ?? false,
    loading: query.isLoading, failed: query.isError, fromArchive: query.data?.from_archive ?? false,
  };
}

/** 审查结果可能落在 report.review 或 review_agent 步骤的 output.review。 */
export function useReview(detail?: TaskDetailResponse): ReviewPayload | null {
  return useMemo(() => reviewOf(detail), [detail]);
}

export function useTaskStep(detail: TaskDetailResponse | undefined, name: string) {
  return useMemo(() => detail?.task.steps?.find((step) => step.name === name), [detail, name]);
}
