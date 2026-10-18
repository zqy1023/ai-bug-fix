import type { ApprovalItem, DashboardSummary, ModelCall, PageResult, RunDetail, RunQuery, RunSummary, ControlAuditItem } from './types';

const API_BASE = '/api/v1';
let apiToken = '';
export function setApiToken(token: string) { apiToken = token.trim(); }
function authorizationHeaders(): Record<string, string> { return apiToken ? { Authorization: 'Bearer ' + apiToken } : {}; }

export class ApiError extends Error {
  constructor(message: string, public readonly status: number, public readonly code = 'API_ERROR') {
    super(message); this.name = 'ApiError';
  }
}
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(API_BASE + path, {
    ...init, credentials: 'same-origin',
    headers: { Accept: 'application/json', ...authorizationHeaders(), ...(init?.body ? { 'Content-Type': 'application/json' } : {}), ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string; message?: string; code?: string } | null;
    throw new ApiError(body?.message ?? body?.detail ?? '请求失败（HTTP ' + response.status + '）', response.status, body?.code);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  health: async () => {
    const response = await fetch('/api/health', { headers: { Accept: 'application/json', ...authorizationHeaders() }, credentials: 'same-origin' });
    if (!response.ok) throw new ApiError('健康检查失败（HTTP ' + response.status + '）', response.status);
    return response.json() as Promise<{ status: string; service: string; version: string; runtime_dir_configured: boolean; source_repo_configured: boolean; api_token_required: boolean; timestamp: string }>;
  },
  dashboard: () => request<DashboardSummary>('/dashboard/summary'),
  runs: (query: RunQuery) => {
    const params = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => { if (value !== undefined && value !== '') params.set(key, String(value)); });
    return request<PageResult<RunSummary>>('/runs?' + params.toString());
  },
  run: (runId: string) => request<RunDetail>('/runs/' + encodeURIComponent(runId)),
  task: (runId: string, taskId: string) => request<import('./types').TaskDetailResponse>('/runs/' + encodeURIComponent(runId) + '/tasks/' + encodeURIComponent(taskId)),
  retryFailedStep: (runId: string, taskId: string, stepName: string, reason = '人工重试失败节点') => request<{ status: string; message: string }>('/runs/' + encodeURIComponent(runId) + '/tasks/' + encodeURIComponent(taskId) + '/steps/' + encodeURIComponent(stepName) + '/retry', { method: 'POST', body: JSON.stringify({ reason }) }),
  artifactContent: (runId: string, path: string, taskId?: string) => {
    const params = new URLSearchParams({ path });
    if (taskId) params.set('task_id', taskId);
    return request<{ path: string; task_id: string; content: unknown }>('/runs/' + encodeURIComponent(runId) + '/artifacts/content?' + params.toString());
  },
  modelCalls: (runId: string, taskId?: string) => {
    const params = new URLSearchParams();
    if (taskId) params.set('task_id', taskId);
    return request<{ items: ModelCall[]; total: number; source: string }>('/runs/' + encodeURIComponent(runId) + '/model-calls?' + params.toString());
  },
  controlAudit: (runId: string) => request<{ items: ControlAuditItem[]; total: number }>('/runs/' + encodeURIComponent(runId) + '/control-audit'),
  approvals: () => request<{ items: ApprovalItem[]; total: number }>('/approvals?status=pending').then((response) => response.items),
  decideApproval: (approvalId: string, decision: 'approve' | 'reject', reason: string) => request<{ run_id: string; task_id: string; decision: string; status: string }>('/approvals/' + encodeURIComponent(approvalId) + '/decision', { method: 'POST', body: JSON.stringify({ decision, reason }) }),
  startRun: (payload: { log_days: number; log_limit: number; review_mode: 'agent' | 'human'; max_attempts: number }) => request<{ run_id: string; status: string }>('/runs', { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify(payload) }),
  pauseRun: (runId: string) => request<{ status: string; message: string }>('/runs/' + encodeURIComponent(runId) + '/pause', { method: 'POST' }),
  resumeRun: (runId: string) => request<{ status: string }>('/runs/' + encodeURIComponent(runId) + '/resume', { method: 'POST' }),
  cancelRun: (runId: string) => request<{ status: string; message: string }>('/runs/' + encodeURIComponent(runId) + '/cancel', { method: 'POST' }),
};
