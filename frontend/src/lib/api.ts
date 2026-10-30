import type {
  AgentCallDetail,
  TaskChanges,
  ArtifactRef,
  CheckpointHistoryResponse,
  CheckpointProjection,
  ControlAuditItem,
  DashboardSummary,
  HealthResponse,
  ModelCall,
  PageResult,
  RunBoard,
  RunConfig,
  RunDefaultsUpdate,
  RunDetail,
  RunParameters,
  RunQuery,
  RunSummary,
  StepDetailResponse,
  TaskDetailResponse,
  OriginalProblemLog,
} from './types';

const API_BASE = '/api/v1';
const REQUEST_TIMEOUT_MS = 60_000;
let apiToken = '';

export function setApiToken(token: string) {
  apiToken = token.trim();
}

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
    return new ApiError(shaped.message ?? `请求失败（HTTP ${status}）`, status, shaped.code ?? 'API_ERROR');
  }
  const message = typeof detail === 'string' ? detail : body?.message ?? `请求失败（HTTP ${status}）`;
  return new ApiError(message, status, body?.code ?? 'API_ERROR');
}

function isTimeoutError(error: unknown): boolean {
  return error instanceof DOMException && (error.name === 'TimeoutError' || error.name === 'AbortError');
}

async function fetchWithTimeout(url: string, init: RequestInit): Promise<Response> {
  const timeout = AbortSignal.timeout(REQUEST_TIMEOUT_MS);
  try {
    return await fetch(url, {
      ...init,
      signal: init.signal ? AbortSignal.any([init.signal, timeout]) : timeout,
    });
  } catch (error) {
    if (isTimeoutError(error)) throw new ApiError('请求超时', 0, 'API_TIMEOUT');
    throw error;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetchWithTimeout(API_BASE + path, {
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
    const body = (await response.json().catch(() => null)) as ErrorBody | null;
    throw errorFrom(body, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function encode(value: string) {
  return encodeURIComponent(value);
}

function queryString(values: Record<string, unknown>): string {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value));
  });
  const text = params.toString();
  return text ? `?${text}` : '';
}

export const api = {
  health: () => request<HealthResponse>('/health'),
  dashboard: () => request<DashboardSummary>('/dashboard/summary'),
  runConfig: () => request<RunConfig>('/run-config'),
  updateRunConfig: (payload: RunDefaultsUpdate) =>
    request<RunConfig>('/run-config', { method: 'PUT', body: JSON.stringify(payload) }),
  resetRunConfig: () => request<RunConfig>('/run-config', { method: 'DELETE' }),

  runs: (query: RunQuery) => request<PageResult<RunSummary>>('/runs' + queryString(query as unknown as Record<string, unknown>)),
  run: (runId: string) => request<RunDetail>(`/runs/${encode(runId)}`),
  board: (runId: string, query: { page?: number; page_size?: number; category?: string; keyword?: string } = {}) =>
    request<RunBoard>(`/runs/${encode(runId)}/board` + queryString(query)),

  startRun: (payload: Partial<RunParameters>) =>
    request<{ run_id: string; status: string }>('/runs', {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify(payload),
    }),
  pauseRun: (runId: string) =>
    request<{ status: string; message: string }>(`/runs/${encode(runId)}/pause`, { method: 'POST' }),
  resumeRun: (runId: string) =>
    request<{ status: string; message?: string }>(`/runs/${encode(runId)}/resume`, { method: 'POST' }),
  retryRun: (runId: string) =>
    request<{ status: string; mode: 'resume' | 'restart'; message?: string }>(`/runs/${encode(runId)}/retry`, {
      method: 'POST',
    }),
  cancelRun: (runId: string) =>
    request<{ status: string; message: string }>(`/runs/${encode(runId)}/cancel`, { method: 'POST' }),

  task: (runId: string, taskId: string) =>
    request<TaskDetailResponse>(`/runs/${encode(runId)}/tasks/${encode(taskId)}`),
  taskChanges: (runId: string, taskId: string) =>
    request<TaskChanges>(`/runs/${encode(runId)}/artifacts/download` + queryString({
      path: 'artifacts/diff.json', task_id: taskId,
    })).then(value => ({ ...value, available: true })).catch((error: unknown) => {
      if (error instanceof ApiError && error.status === 404) {
        return { available: false, files: [], reason: '尚无完整源码差异归档' } as TaskChanges;
      }
      throw error;
    }),
  problemLog: (runId: string, taskId: string) =>
    request<OriginalProblemLog>(`/runs/${encode(runId)}/tasks/${encode(taskId)}/problem-log`),
  retryTask: (runId: string, taskId: string) =>
    request<{ status: string; attempt?: number; message?: string; run_id?: string; task_id?: string }>(
      `/runs/${encode(runId)}/tasks/${encode(taskId)}/retry`,
      { method: 'POST' },
    ),
  step: (runId: string, taskId: string, stepName: string) =>
    request<StepDetailResponse>(`/runs/${encode(runId)}/tasks/${encode(taskId)}/steps/${encode(stepName)}`),
  runStep: (runId: string, taskId: string, stepName: string, confirmWrite: boolean, continueAfter = false) =>
    request<{ run_id: string; task_id: string; step: string; status: string; accepted: boolean; started_at: string }>(
      `/runs/${encode(runId)}/tasks/${encode(taskId)}/steps/${encode(stepName)}/run`,
      { method: 'POST', body: JSON.stringify({ confirm_write: confirmWrite, continue_after: continueAfter }) },
    ),

  checkpoint: (runId: string) =>
    request<{ run_id: string; checkpoint: CheckpointProjection }>(`/runs/${encode(runId)}/checkpoint`),
  checkpoints: (runId: string, limit = 30) =>
    request<CheckpointHistoryResponse>(`/runs/${encode(runId)}/checkpoints?limit=${limit}`),
  controlAudit: (runId: string) =>
    request<{ items: ControlAuditItem[]; total: number }>(`/runs/${encode(runId)}/control-audit`),

  artifacts: (runId: string) => request<{ items: ArtifactRef[]; total: number }>(`/runs/${encode(runId)}/artifacts`),
  artifactContent: (runId: string, path: string, taskId?: string) =>
    request<{ path: string; task_id: string; content: unknown }>(
      `/runs/${encode(runId)}/artifacts/content` + queryString({ path, task_id: taskId }),
    ),
  downloadArtifact: async (runId: string, path: string, taskId?: string) => {
    const response = await fetchWithTimeout(
      API_BASE + `/runs/${encode(runId)}/artifacts/download` + queryString({ path, task_id: taskId }),
      { headers: authorizationHeaders(), credentials: 'same-origin' },
    );
    if (!response.ok) throw new ApiError('下载产物失败', response.status);
    return response.blob();
  },

  modelCalls: (runId: string, taskId?: string) =>
    request<{ items: ModelCall[]; total: number; source: string }>(
      `/runs/${encode(runId)}/model-calls` + queryString({ task_id: taskId }),
    ),
  agentCall: (runId: string, taskId: string, modelCallId: string) =>
    request<AgentCallDetail>(`/runs/${encode(runId)}/tasks/${encode(taskId)}/agent-calls/${encode(modelCallId)}`),
};
