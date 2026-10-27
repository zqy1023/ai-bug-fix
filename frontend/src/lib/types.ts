import type { ExecutionResult, Assessment, ActionAvailability, DeliveryView } from './workflow.generated';
export type { ExecutionResult, Assessment, ActionAvailability, DeliveryView } from './workflow.generated';

export type StepStatus = 'pending' | 'running' | 'waiting' | 'succeeded' | 'failed' | 'skipped';
export type RunStatus =
  | 'queued' | 'running' | 'waiting_approval' | 'succeeded' | 'partial_failed'
  | 'failed' | 'paused' | 'pause_requested' | 'cancelled' | 'cancel_requested';
export type BusinessResult =
  | 'UNVERIFIED' | 'BLOCKED'
  | 'LOCATED' | 'INSUFFICIENT_EVIDENCE' | 'MANUAL' | 'PASS' | 'FAIL' | 'REJECTED'
  | 'SKIPPED' | 'DELIVERED' | 'SUCCESS' | 'FAILED' | null;

export interface RunParameters {
  log_hours: number;
  log_limit: number;
  review_mode: 'agent' | 'human';
  max_attempts: number;
}

export interface RunConfig {
  defaults: RunParameters;
  limits: { max_log_hours: number; max_attempts: number };
}

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
  /** 终态由服务端落盘报告确认；false 时前端必须继续轮询/保持 SSE。 */
  terminal?: boolean;
  identified_tasks?: number;
  dispatched_tasks?: number;
  started_at: string;
  finished_at?: string | null;
  task_count: number;
  succeeded_tasks: number;
  failed_tasks: number;
  blocked_tasks: number;
  manual_tasks: number;
  waiting_tasks: number;
  inactive_tasks?: number;
  operator?: string | null;
}

export interface ArtifactRef {
  name: string;
  path: string;
  media_type?: string;
  size_bytes?: number | null;
  description?: string;
  task_id?: string;
}

export interface StepRecord {
  execution?: ExecutionResult | null;
  assessment?: Assessment | null;
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

export interface WorkflowReport {
  report_version?: number;
  report_kind?: 'run_final' | 'task_final' | string;
  run_id?: string;
  task_id?: string;
  carrier?: string;
  biztype?: string;
  result?: string;
  status?: string;
  terminal_status?: string;
  terminal_step?: string;
  terminal_reason?: string;
  next_action?: string;
  finished_at?: string;
  elapsed_ms?: number;
  tokens?: number;
  change_summary?: string;
  root_cause?: string;
  completed_steps?: string[];
  legacy_report?: boolean;
}

export interface TaskSummary {
  task_id: string;
  carrier: string;
  biztype: string;
  result: string;
  terminal_status?: string;
  terminal_step?: string;
  terminal_reason?: string;
  next_action?: string;
  steps_count?: number;
  duration_ms?: number | null;
  approval_pending?: boolean;
  current_step?: string;
  attempt?: number;
  lifecycle?: {
    status?: string; current_step?: string; attempt?: number; terminal_step?: string;
    terminal_reason?: string; next_action?: string; last_error_summary?: string;
    updated_at?: string;
  };
  problem?: Record<string, unknown>;
  report?: WorkflowReport;
  steps?: StepRecord[];
}

export interface TaskDetailResponse {
  read_only?: boolean;
  actions?: { retry: ActionAvailability };
  delivery?: DeliveryView;
  run_id: string;
  run_status: RunStatus;
  worker_active?: boolean;
  run_terminal?: boolean;
  stages?: WorkflowStage[];
  run_error?: {
    status?: string;
    error_type?: string;
    message?: string;
    failed_at?: string;
    terminal_step?: string;
    terminal_reason?: string;
    next_action?: string;
  };
  config: Record<string, unknown>;
  task: TaskSummary & { steps: StepRecord[]; report: WorkflowReport };
  artifacts: ArtifactRef[];
}

export interface WorkflowStage {
  key: string;
  title: string;
  members: string[];
  group: boolean;
}

export interface RunDetail extends RunSummary {
  read_only?: boolean;
  actions?: Record<'pause' | 'resume' | 'retry' | 'cancel', ActionAvailability>;
  worker_active?: boolean;
  phase?: string;
  run_lifecycle?: {
    status?: string; phase?: string; current_step?: string; current_task_id?: string; attempt?: number;
    heartbeat_at?: string; error_type?: string; error_message?: string;
    terminal_reason?: string; next_action?: string; updated_at?: string;
    progress?: { step: string; message: string; counts: Record<string, number> };
    progress_at?: string;
  };
  run_error?: {
    status?: string; error_type?: string; message?: string; failed_at?: string;
    terminal_step?: string; terminal_reason?: string; next_action?: string;
  };
  run_report?: WorkflowReport;
  config: Record<string, unknown>;
  steps: StepRecord[];
  tasks: TaskSummary[];
  checkpoint?: CheckpointProjection & { resumable: boolean };
  artifacts: ArtifactRef[];
}

export interface CheckpointProjection {
  checkpoint_id?: string | null;
  parent_checkpoint_id?: string | null;
  created_at?: string | null;
  next_nodes: string[];
  node?: string | null;
  has_interrupt?: boolean;
}

export interface CheckpointHistoryResponse {
  run_id: string;
  items: CheckpointProjection[];
  total: number;
}

export type ApprovalRetryTarget =
  | 'locate_agent'
  | 'repair_agent'
  | 'review_agent'
  | 'prepare_delivery';

export interface ApprovalItem {
  delivery?: DeliveryView;
  actions?: { approve: ActionAvailability; reject: ActionAvailability };
  approval_id: string;
  run_id: string;
  task_id: string;
  carrier: string;
  biztype: string;
  scope: 'carrier' | 'project';
  status: 'pending' | 'approved' | 'rejected' | 'expired';
  summary: string;
  changed_files: string[];
  business_recovery?: {
    status: 'verified' | 'unverified' | 'failed' | 'not_executed';
    reason: string;
  };
  change_plan?: {
    scope: 'carrier' | 'project';
    rationale: string;
    edits: Array<{ path: string; action: string; note: string }>;
  };
  created_at: string;
  expires_at?: string | null;
}

export interface RunQuery {
  page: number;
  page_size: number;
  status?: RunStatus | '';
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

export interface ControlAuditItem {
  id: number;
  run_id: string;
  task_id: string;
  action: string;
  actor: string;
  reason: string;
  created_at: string;
}

export type AgentEvent =
  | { type: 'assistant'; text?: string; final?: boolean }
  | { type: 'tool_use' | 'tool_decision'; tool_use_id?: string | null; tool?: string; input?: Record<string, unknown>; elapsed_ms?: number; decision?: string; reason?: string; source_sha256?: string }
  | { type: 'tool_result'; tool_use_id?: string | null; is_error?: boolean; text?: string; elapsed_ms?: number };

export interface PromptSnapshot {
  model_call_id: string;
  run_id: string;
  task_id: string;
  agent_role: string;
  attempt: number;
  model: string;
  prompt_version: string;
  prompt_hash: string;
  system_prompt_common: string;
  system_prompt_role: string;
  task_prompt: string;
  prompt_context: string;
  full_task_context?: string;
  chars: Record<string, number>;
  truncated: boolean;
  redacted: boolean;
  context_mode?: 'full' | 'delta';
  logical_context_hash?: string;
  previous_context_hash?: string;
}

export interface ModelCall {
  model_call_id: string;
  run_id: string;
  task_id: string;
  agent_role: string;
  attempt: number;
  provider?: string;
  model: string;
  prompt_version?: string;
  prompt_hash?: string;
  logical_context_hash?: string;
  sent_prompt_hash?: string;
  context_mode?: 'full' | 'delta';
  previous_context_hash?: string;
  prompt_snapshot?: string;
  started_at: string;
  finished_at?: string;
  duration_ms?: number;
  status: string;
  input_tokens?: number | null;
  output_tokens?: number | null;
  cache_tokens?: number | null;
  total_tokens?: number | null;
  tool_call_count?: number | null;
  tool_allowed?: number | null;
  tool_denied?: number | null;
  tool_completed?: number | null;
  structured_output_calls?: number | null;
  usage_source?: string;
  sdk_receipt?: Record<string, unknown>;
  token_total_scope?: string;
  prompt_chars?: number | null;
  result_chars?: number | null;
  error_type?: string;
  error_detail?: string;
  event_count?: number;
  event_persistence_status?: 'persisted' | 'empty' | 'failed';
  events_path?: string;
  events?: AgentEvent[];
  output_preview?: string;
}

export interface AgentCallDetail {
  run_id: string;
  task_id: string;
  call: ModelCall;
  prompt_snapshot: PromptSnapshot | Record<string, never>;
  prompt_snapshot_available: boolean;
  events: AgentEvent[];
  event_total: number;
}

export interface BoardTask {
  task_id: string;
  carrier: string;
  biztype: string;
  service_name: string;
  result: string;
  terminal_step: string;
  terminal_reason: string;
  next_action: string;
  current_step?: string;
  attempt?: number;
  lifecycle?: Record<string, unknown>;
  approval_pending: boolean;
  steps_count: number;
  duration_ms?: number | null;
  step_statuses: Record<string, StepStatus>;
}

export interface RunBoard {
  counts?: Record<string, number>;
  all_total?: number;
  page?: number;
  page_size?: number;
  run_id: string;
  summary: RunSummary;
  stages?: WorkflowStage[];
  tasks: BoardTask[];
  total: number;
}

export interface StepContext {
  attempt: number;
  status: StepStatus;
  result: BusinessResult;
  changed_files: string[];
  change_plan_scope: string;
  delivery_status: string;
  counts: Record<string, number>;
  flags: Record<string, boolean>;
}

export interface StepDetailResponse {
  actions?: { rerun: ActionAvailability; continue_after: ActionAvailability };
  run_id: string;
  task_id: string;
  step: StepRecord;
  artifacts: ArtifactRef[];
  model_call_ids: string[];
  context: StepContext;
}
