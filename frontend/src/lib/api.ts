import type { ApprovalItem, DashboardSummary, ModelCall, PageResult, RunDetail, RunQuery, RunSummary, ControlAuditItem } from './types';

const API_BASE = '/api/v1';

export class ApiError extends Error {
  constructor(message: string, public readonly status: number, public readonly code = 'API_ERROR') {
    super(message); this.name = 'ApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init, credentials: 'same-origin',
    headers: { Accept: 'application/json', ...(init?.body ? { 'Content-Type': 'application/json' } : {}), ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string; message?: string; code?: string } | null;
    throw new ApiError(body?.message ?? body?.detail ?? `请求失败（HTTP ${response.status}）`, response.status, body?.code);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  dashboard: () => request<DashboardSummary>('/dashboard/summary'),
  runs: (query: RunQuery) => {
    const params = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => { if (value !== undefined && value !== '') params.set(key, String(value)); });
    return request<PageResult<RunSummary>>(`/runs?${params.toString()}`);
  },
  run: (runId: string) => request<RunDetail>(`/runs/${encodeURIComponent(runId)}`),
  modelCalls: (runId: string, taskId?: string) => {
    const params = new URLSearchParams();
    if (taskId) params.set('task_id', taskId);
    return request<{ items: ModelCall[]; total: number; source: string }>(`/runs/${encodeURIComponent(runId)}/model-calls?${params.toString()}`);
  },
  controlAudit: (runId: string) => request<{ items: ControlAuditItem[]; total: number }>(`/runs/${encodeURIComponent(runId)}/control-audit`),
  approvals: () => request<{ items: ApprovalItem[]; total: number }>('/approvals?status=pending').then((response) => response.items),
  decideApproval: (approvalId: string, decision: 'approve' | 'reject', reason: string) =>
    request<{ run_id: string; task_id: string; decision: string; status: string }>(`/approvals/${encodeURIComponent(approvalId)}/decision`, {
      method: 'POST', body: JSON.stringify({ decision, reason }),
    }),
  startRun: (payload: { log_days: number; log_limit: number; review_mode: 'agent' | 'human'; max_attempts: number }) =>
    request<{ run_id: string; status: string }>('/runs', { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify(payload) }),
  resumeRun: (runId: string) => request<{ status: string }>(`/runs/${encodeURIComponent(runId)}/resume`, { method: 'POST' }),
  cancelRun: (runId: string) => request<{ status: string; message: string }>(`/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' }),
};
