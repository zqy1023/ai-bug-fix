export type StepStatus = 'pending' | 'running' | 'waiting' | 'succeeded' | 'failed' | 'skipped';
export type BusinessResult = 'passed' | 'failed' | 'rejected' | 'manual' | 'pending' | null;
export type RunStatus = 'queued' | 'running' | 'waiting_approval' | 'succeeded' | 'partial_failed' | 'failed' | 'cancelled' | 'cancel_requested';

export interface DashboardSummary {
  active_runs: number;
  waiting_approvals: number;
  succeeded_24h: number;
  failed_24h: number;
  success_rate_7d: number;
  recent_runs: RunSummary[];
  daily_trend: Array<{ date: string; succeeded: number; failed: number }>;
}

export interface RunSummary {
  run_id: string;
  status: RunStatus;
  started_at: string;
  finished_at?: string | null;
  task_count: number;
  succeeded_tasks: number;
  failed_tasks: number;
  waiting_tasks: number;
  operator?: string | null;
}

export interface StepRecord {
  name: string;
  title: string;
  status: StepStatus;
  result?: BusinessResult;
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  attempt?: number;
  summary?: string;
  error?: { code?: string; message?: string; retryable?: boolean } | null;
  artifacts?: ArtifactRef[];
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
}

export interface ArtifactRef {
  name: string;
  path: string;
  media_type?: string;
  size_bytes?: number;
  description?: string;
  task_id?: string;
}

export interface TaskSummary {
  task_id: string;
  carrier: string;
  biztype: string;
  result: string;
  terminal_status?: string;
  steps: StepRecord[];
}

export interface RunDetail extends RunSummary {
  config: Record<string, unknown>;
  steps: StepRecord[];
  tasks: TaskSummary[];
  checkpoint?: { resumable: boolean; next_nodes: string[] };
  artifacts: ArtifactRef[];
}

export interface ApprovalItem {
  approval_id: string;
  run_id: string;
  task_id: string;
  carrier: string;
  biztype: string;
  scope: 'carrier' | 'project';
  status: 'pending' | 'approved' | 'rejected' | 'expired';
  summary: string;
  changed_files: string[];
  created_at: string;
  expires_at?: string | null;
}

export interface RunQuery {
  page: number;
  page_size: number;
  status?: RunStatus;
  keyword?: string;
  from?: string;
  to?: string;
}

export interface PageResult<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface ModelCall {
  model_call_id: string; run_id: string; task_id: string; agent_role: string; attempt: number;
  provider?: string; model: string; prompt_version?: string; prompt_hash?: string;
  started_at: string; finished_at?: string; duration_ms?: number; status: string;
  input_tokens?: number | null; output_tokens?: number | null; cache_tokens?: number | null;
  total_tokens?: number | null; tool_call_count?: number | null; error_type?: string;
}
export interface ControlAuditItem {
  id: number; run_id: string; task_id: string; action: string; actor: string; reason: string; created_at: string;
}
export interface ApiErrorShape {
  code: string;
  message: string;
  request_id?: string;
}