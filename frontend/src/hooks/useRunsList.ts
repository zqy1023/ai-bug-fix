import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import type { RunStatus, RunSummary } from '../lib/types';

export type RunListView = RunStatus | 'active' | 'ended' | '';
const ACTIVE = new Set<RunStatus>(['queued', 'running', 'pause_requested', 'cancel_requested']);
const ENDED = new Set<RunStatus>(['succeeded', 'partial_failed', 'failed', 'cancelled']);

/** Keep group counts and pagination consistent with the existing exact-status API. */
export function useRunsList({ page, pageSize, view, keyword, from }: Readonly<{
  page: number; pageSize: number; view: RunListView; keyword: string; from?: string;
}>) {
  const query = useQuery({
    queryKey: queryKeys.runs({ page: 1, status: 'list-index', keyword, from }),
    queryFn: async () => {
      const items: RunSummary[] = [];
      let current = 1;
      // The server supports exact states only. Read summary pages, never task payloads.
      // A single request covers the normal workbench history (up to 100 batches).
      while (true) {
        const response = await api.runs({ page: current, page_size: 100, keyword: keyword || undefined, from });
        items.push(...response.items);
        if (!response.items.length || items.length >= response.total) break;
        current += 1;
      }
      return [...new Map(items.map((run) => [run.run_id, run])).values()];
    },
    placeholderData: keepPreviousData,
    refetchInterval: 5000,
  });
  const index = query.data ?? [];
  const isActive = (run: RunSummary) => ACTIVE.has(run.status);
  const isEnded = (run: RunSummary) => run.terminal === true || (run.terminal === undefined && ENDED.has(run.status));
  const filtered = index.filter((run) => view === 'active' ? isActive(run) : view === 'ended' ? isEnded(run) : !view || run.status === view);
  return {
    query, items: filtered.slice((page - 1) * pageSize, page * pageSize), total: filtered.length,
    activeCount: index.filter(isActive).length, endedCount: index.filter(isEnded).length,
    attentionCount: index.reduce((sum, run) => sum + run.failed_tasks + run.blocked_tasks + run.manual_tasks, 0),
    attentionRun: index.find((run) => run.failed_tasks + run.blocked_tasks + run.manual_tasks > 0),
  };
}
