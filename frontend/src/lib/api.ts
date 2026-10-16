import type {
  ApprovalItem, DashboardSummary, PageResult, RunDetail, RunQuery, RunSummary,
} from './types';

const DEMO_MODE = import.meta.env.VITE_WORKBENCH_DEMO_MODE === 'true';
const API_BASE = '/api/v1';

export class ApiError extends Error {
  constructor(message: string, public readonly status: number, public readonly code = 'API_ERROR') {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: 'same-origin',
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string; message?: string; code?: string; request_id?: string } | null;
    throw new ApiError(body?.message ?? body?.detail ?? `请求失败（HTTP ${response.status}）`, response.status, body?.code);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

const demoRuns: RunSummary[] = [
  { run_id: 'demo-20261015-001', status: 'running', started_at: new Date(Date.now() - 8 * 60_000).toISOString(), task_count: 4, succeeded_tasks: 1, failed_tasks: 0, waiting_tasks: 1, operator: '演示数据' },
  { run_id: 'demo-20261015-002', status: 'waiting_approval', started_at: new Date(Date.now() - 42 * 60_000).toISOString(), task_count: 2, succeeded_tasks: 1, failed_tasks: 0, waiting_tasks: 1, operator: '演示数据' },
  { run_id: 'demo-20261014-003', status: 'succeeded', started_at: new Date(Date.now() - 3 * 60 * 60_000).toISOString(), finished_at: new Date(Date.now() - 2 * 60 * 60_000).toISOString(), task_count: 3, succeeded_tasks: 3, failed_tasks: 0, waiting_tasks: 0, operator: '演示数据' },
];

const demoDashboard: DashboardSummary = {
  active_runs: 1, waiting_approvals: 1, succeeded_24h: 18, failed_24h: 3, success_rate_7d: 0.86,
  recent_runs: demoRuns,
  daily_trend: [
    { date: '10-09', succeeded: 12, failed: 4 }, { date: '10-10', succeeded: 15, failed: 2 },
    { date: '10-11', succeeded: 10, failed: 3 }, { date: '10-12', succeeded: 17, failed: 1 },
    { date: '10-13', succeeded: 13, failed: 2 }, { date: '10-14', succeeded: 16, failed: 4 },
    { date: '10-15', succeeded: 18, failed: 3 },
  ],
};

export const api = {
  dashboard: () => DEMO_MODE ? Promise.resolve(demoDashboard) : request<DashboardSummary>('/dashboard/summary'),
  runs: (query: RunQuery) => {
    if (DEMO_MODE) {
      const filtered = demoRuns.filter((r) => (!query.status || r.status === query.status) && (!query.keyword || r.run_id.includes(query.keyword)));
      return Promise.resolve({ items: filtered, total: filtered.length, page: query.page, page_size: query.page_size } satisfies PageResult<RunSummary>);
    }
    const params = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => { if (value !== undefined && value !== '') params.set(key, String(value)); });
    return request<PageResult<RunSummary>>(`/runs?${params.toString()}`);
  },
  run: (runId: string) => DEMO_MODE ? Promise.resolve({
    ...demoRuns[0],
    run_id: runId,
    config: { review_mode: 'agent', max_attempts: 3, log_days: 1 },
    steps: [
      { name: 'discover_logs', title: '发现异常日志', status: 'succeeded', result: 'passed', duration_ms: 1320, summary: '发现 4 条候选异常' },
      { name: 'normalize_error', title: '规范化异常', status: 'succeeded', result: 'passed', duration_ms: 23, summary: '已提取异常签名' },
      { name: 'deduplicate_error', title: '异常去重', status: 'succeeded', result: 'passed', duration_ms: 18, summary: '归并为 4 个问题' },
      { name: 'identify_carrier', title: '识别运输商', status: 'succeeded', result: 'passed', duration_ms: 48, summary: '识别到 4 个任务' },
      { name: 'claim_repair_slot', title: '领取修复资格', status: 'succeeded', result: 'passed', duration_ms: 6, summary: '已领取独立修复资格' },
      { name: 'prepare_source_snapshot', title: '准备源码快照', status: 'succeeded', result: 'passed', duration_ms: 420, summary: '已建立基线快照' },
      { name: 'prepare_evidence', title: '准备分析证据', status: 'running', result: null, summary: '正在提取异常上下文' },
    ],
    tasks: [{ task_id: 'Brazil__framework.track_server__demo', carrier: 'Brazil', biztype: 'framework.track_server', result: 'RUNNING', steps: [] }],
    artifacts: [],
    checkpoint: { resumable: false, next_nodes: [] },
  } satisfies RunDetail) : request<RunDetail>(`/runs/${encodeURIComponent(runId)}`),
  approvals: () => DEMO_MODE ? Promise.resolve([] as ApprovalItem[]) : request<{ items: ApprovalItem[]; total: number }>('/approvals?status=pending').then((response) => response.items),
  decideApproval: (approvalId: string, decision: 'approve' | 'reject', reason: string) =>
    request<void>(`/approvals/${encodeURIComponent(approvalId)}/decision`, {
      method: 'POST', body: JSON.stringify({ decision, reason }),
    }),
  startRun: (payload: { log_days: number; log_limit: number; review_mode: 'agent' | 'human'; max_attempts: number }) =>
    request<{ run_id: string }>('/runs', { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify(payload) }),
  resumeRun: (runId: string) => request<void>(`/runs/${encodeURIComponent(runId)}/resume`, { method: 'POST' }),
  cancelRun: (runId: string) => request<void>(`/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' }),
};

export const isDemoMode = DEMO_MODE;