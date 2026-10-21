import type {
  AgentCallDetail, ApprovalItem, ApprovalRetryTarget, CheckpointHistoryResponse, CheckpointProjection, ControlAuditItem,
  DashboardSummary, ModelCall, PageResult, RunBoard, RunDetail, RunQuery, RunSummary, StepDetailResponse,
  TaskDetailResponse,
} from './types';

const API_BASE = '/api/v1';
let apiToken = '';

export function setApiToken(token: string) { apiToken = token.trim(); }
export function authorizationHeaders(): Record<string, string> {
  return apiToken ? { Authorization: 'Bearer ' + apiToken } : {};
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code = 'API_ERROR',
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

type ErrorBody = { detail?: unknown; message?: string; code?: string };

function errorFrom(body: ErrorBody | null, status: number): ApiError {
  const detail = body?.detail;
  if (detail && typeof detail === 'object') {
    const shaped = detail as { code?: string; message?: string };
    return new ApiError(shaped.message ?? '请求失败（HTTP ' + status + '）', status, shaped.code ?? 'API_ERROR');
  }
  const message = typeof detail === 'string' ? detail : body?.message ?? '请求失败（HTTP ' + status + '）';
  return new ApiError(message, status, body?.code ?? 'API_ERROR');
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(API_BASE + path, {
    ...init,
    credentials: 'same-origin',
    headers: {
      Accept: 'application/json',
      ...authorizationHeaders(),
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as ErrorBody | null;
    throw errorFrom(body, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function encode(value: string) { return encodeURIComponent(value); }

export const api = {
  health: async () => {
    const response = await fetch(API_BASE + '/health', {
      headers: { Accept: 'application/json', ...authorizationHeaders() },
      credentials: 'same-origin',
    });
    if (!response.ok) throw new ApiError('健康检查失败（HTTP ' + response.status + '）', response.status);
    return response.json() as Promise<{
      status: string; service: string; version: string;
      runtime_dir_configured: boolean; source_repo_configured: boolean;
      api_token_required: boolean; timestamp: string;
    }>;
  },
  dashboard: () => request<DashboardSummary>('/dashboard/summary'),
  runs: (query: RunQuery) => {
    const params = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== '') params.set(key, String(value));
    });
    return request<PageResult<RunSummary>>('/runs?' + params.toString());
  },
  run: (runId: string) => request<RunDetail>('/runs/' + encode(runId)),
  board: (runId: string) => request<RunBoard>('/runs/' + encode(runId) + '/board'),
  checkpoint: (runId: string) =>
    request<{ run_id: string; checkpoint: CheckpointProjection }>('/runs/' + encode(runId) + '/checkpoint'),
  checkpoints: (runId: string, limit = 30) =>
    request<CheckpointHistoryResponse>('/runs/' + encode(runId) + '/checkpoints?limit=' + limit),
  task: (runId: string, taskId: string) =>
    request<TaskDetailResponse>('/runs/' + encode(runId) + '/tasks/' + encode(taskId)),
  step: (runId: string, taskId: string, stepName: string) =>
    request<StepDetailResponse>('/runs/' + encode(runId) + '/tasks/' + encode(taskId) + '/steps/' + encode(stepName)),
  runStep: (runId: string, taskId: string, stepName: string, confirmWrite: boolean) =>
    request<{ run_id: string; task_id: string; step: string; status: string; accepted: boolean; started_at: string }>(
      '/runs/' + encode(runId) + '/tasks/' + encode(taskId) + '/steps/' + encode(stepName) + '/run',
      { method: 'POST', body: JSON.stringify({ confirm_write: confirmWrite }) },
    ),
  artifactContent: (runId: string, path: string, taskId?: string) => {
    const params = new URLSearchParams({ path });
    if (taskId) params.set('task_id', taskId);
    return request<{ path: string; task_id: string; content: unknown }>(
      '/runs/' + encode(runId) + '/artifacts/content?' + params.toString(),
    );
  },
  modelCalls: (runId: string, taskId?: string) => {
    const params = new URLSearchParams();
    if (taskId) params.set('task_id', taskId);
    return request<{ items: ModelCall[]; total: number; source: string }>(
      '/runs/' + encode(runId) + '/model-calls?' + params.toString(),
    );
  },
  agentCall: (runId: string, taskId: string, modelCallId: string) =>
    request<AgentCallDetail>(
      '/runs/' + encode(runId) + '/tasks/' + encode(taskId) + '/agent-calls/' + encode(modelCallId),
    ),
  controlAudit: (runId: string) =>
    request<{ items: ControlAuditItem[]; total: number }>('/runs/' + encode(runId) + '/control-audit'),
  approvals: () => request<{ items: ApprovalItem[]; total: number }>('/approvals?status=pending')
    .then((response) => response.items),
  decideApproval: (
    approvalId: string,
    decision: 'approve' | 'reject',
    reason: string,
    retryTarget: ApprovalRetryTarget = 'repair_agent',
  ) =>
    request<{ run_id: string; task_id: string; decision: string; status: string }>(
      '/approvals/' + encode(approvalId) + '/decision',
      {
        method: 'POST',
        body: JSON.stringify({
          decision,
          reason,
          retry_target: retryTarget,
        }),
      },
    ),
  startRun: (payload: { log_days: number; log_limit: number; review_mode: 'agent' | 'human'; max_attempts: number }) =>
    request<{ run_id: string; status: string }>('/runs', {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify(payload),
    }),
  pauseRun: (runId: string) =>
    request<{ status: string; message: string }>('/runs/' + encode(runId) + '/pause', { method: 'POST' }),
  resumeRun: (runId: string) =>
    request<{ status: string; message?: string }>('/runs/' + encode(runId) + '/resume', { method: 'POST' }),
  retryRun: (runId: string) =>
    request<{ status: string; mode: 'resume' | 'restart'; message?: string }>('/runs/' + encode(runId) + '/retry', { method: 'POST' }),
  retryTask: (runId: string, taskId: string) =>
    request<{ status: string; attempt: number; message?: string }>(
      '/runs/' + encode(runId) + '/tasks/' + encode(taskId) + '/retry',
      { method: 'POST' },
    ),
  cancelRun: (runId: string) =>
    request<{ status: string; message: string }>('/runs/' + encode(runId) + '/cancel', { method: 'POST' }),
};
