import { queryOptions } from '@tanstack/react-query';
import { api } from './api';
import { queryKeys } from './queryKeys';

/** 页面共享缓存、刷新周期和数据读取；组件只关心展示与交互。 */
export const workbenchQueries = {
  approvals: () => queryOptions({
    queryKey: queryKeys.approvals, queryFn: api.approvals, refetchInterval: 15000,
  }),
  task: (runId: string, taskId: string) => queryOptions({
    queryKey: queryKeys.task(runId, taskId), queryFn: () => api.task(runId, taskId),
    enabled: Boolean(runId && taskId),
    refetchInterval: (query) => query.state.data?.run_terminal && !query.state.data.worker_active ? false : 5000,
  }),
  run: (runId: string) => queryOptions({
    queryKey: queryKeys.run(runId), queryFn: () => api.run(runId), enabled: Boolean(runId),
    refetchInterval: (query) => query.state.data?.terminal ? false : 5000,
  }),
  step: (runId: string, taskId: string, name: string) => queryOptions({
    queryKey: queryKeys.step(runId, taskId, name), queryFn: () => api.step(runId, taskId, name),
    enabled: Boolean(runId && taskId && name),
  }),
};
