import type {
  ActionAvailability,
  AgentPolicy,
  Assessment,
  DeliveryView,
  ExecutionResult,
} from './workflow.generated';

export type {
  ActionAvailability,
  AgentPolicy,
  Assessment,
  DeliveryView,
  ExecutionResult,
} from './workflow.generated';

export type StepStatus = 'pending' | 'running' | 'waiting' | 'succeeded' | 'failed' | 'skipped';

export type RunStatus =
  | 'queued'
  | 'running'
  | 'waiting_approval'
  | 'succeeded'
  | 'partial_failed'
  | 'failed'
  | 'paused'
  | 'pause_requested'
  | 'cancelled'
  | 'cancel_requested';

export type Tone = 'ok' | 'warn' | 'danger' | 'info' | 'idle';

export interface RunParameters {
  agent_policy: AgentPolicy;
  log_hours: number;
  log_limit: number;
  review_mode: 'agent' | 'human';
  max_attempts: number;
}

export interface ModelOption {
  key: string;
  model_id: string;
  label: string;
  roles: string[];
  available: boolean;
  reason?: string | null;
  is_default?: boolean;
}

export interface RunConfig {
  defaults: RunParameters;
  limits: { max_log_hours: number; max_attempts: number };
  models: Record<string, ModelOption[]>;
  model_catalog?: ModelOption[];
  overrides?: RunDefaultsUpdate;
  customized?: boolean;
  nacos?: { enabled: boolean; data_id: string; group: string };
}

export interface RunDefaultsUpdate {
  log_hours?: number | null;
  log_limit?: number | null;
  max_attempts?: number | null;
  unified_model?: string | null;
  review_model?: string | null;
}

export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  runtime_dir_configured: boolean;
  source_repo_configured: boolean;
  api_token_required: boolean;
  timestamp: string;
}

export interface DashboardSummary {
  active_runs: number;
  /** 历史字段；审批已移除，只用于兼容旧 Run。 */
  waiting_approvals: number;
  succeeded_24h: number;
  failed_24h: number;
  success_rate_7d: number;
  recent_runs: RunSummary[];
  daily_trend: Array<{ date: string; succeeded: number; failed: number }>;
  unreadable_runs?: number;
  degraded?: boolean;
}

export interface RunSummary {
  run_id: string;
  status: RunStatus;
  terminal?: boolean;
  identified_tasks?: number;
  dispatched_tasks?: number;
  started_at: string;
  finished_at?: string | null;
  task_count: number;
  succeeded_tasks: number;
  changed_delivered_tasks: number;
  unchanged_delivered_tasks: number;
  failed_tasks: number;
  blocked_tasks: number;
  manual_tasks: number;
  waiting_tasks: number;
  skipped_tasks?: number;
  inactive_tasks?: number;
  pending_tasks?: number;
  running_tasks?: number;
  cancelled_tasks?: number;
  completed_tasks?: number;
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
  result?: string | null;
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

export interface ReviewCoverage {
  sufficient?: boolean;
  missing_evidence?: string[];
  notes?: string;
  [key: string]: unknown;
}

export interface ReviewPayload {
  review_kind?: 'patch' | 'analysis_disposition';
  disposition_verified?: boolean;
  semantic_verified?: boolean;
  verdict?: 'PASS' | 'FAIL' | 'MANUAL' | string;
  code_verdict?: 'PASS' | 'FAIL' | 'UNKNOWN' | string;
  coverage?: ReviewCoverage;
  dimensions?: Record<string, { status?: string; detail?: string; [key: string]: unknown }>;
  summary?: string;
  findings?: string[];
  required_changes?: string[];
  todo?: string[];
  unresolved_issues?: string[];
  delivery_eligible?: boolean;
  [key: string]: unknown;
}

export interface WorkflowReport {
  schema_version?: number;
  report_kind?: string;
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
  changed_files?: string[];
  root_cause?: string;
  change_summary?: string;
  review?: ReviewPayload;
  delivery_status?: string;
  [key: string]: unknown;
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
  step_statuses?: Record<string, string>;
  duration_ms?: number | null;
  approval_pending?: boolean;
  current_step?: string;
  attempt?: number;
  lifecycle?: {
    status?: string;
    current_step?: string;
    attempt?: number;
    terminal_step?: string;
    terminal_reason?: string;
    next_action?: string;
    last_error_summary?: string;
    updated_at?: string;
    started_at?: string;
    finished_at?: string;
  };
  problem?: ProblemPayload;
  report?: WorkflowReport;
  steps?: StepRecord[];
}

export interface ProblemPayload {
  service_name?: string;
  biztype?: string;
  carrier?: string;
  req?: string;
  error?: string;
  raw_error?: string;
  message?: string;
  track_config?: string;
  [key: string]: unknown;
}

export interface OriginalProblemLog {
  message: string;
  trigger_message: string;
  related_messages: string[];
  truncated: boolean;
  from_archive: boolean;
  source_biztype: string;
  message_format: string;
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
    status?: string;
    phase?: string;
    current_step?: string;
    current_task_id?: string;
    attempt?: number;
    heartbeat_at?: string;
    error_type?: string;
    error_message?: string;
    terminal_reason?: string;
    next_action?: string;
    updated_at?: string;
    progress?: { step: string; message: string; counts: Record<string, number> };
    progress_at?: string;
  };
  run_error?: {
    status?: string;
    error_type?: string;
    message?: string;
    failed_at?: string;
    terminal_step?: string;
    terminal_reason?: string;
    next_action?: string;
  };
  run_report?: WorkflowReport;
  config: Record<string, unknown>;
  steps: StepRecord[];
  stages?: WorkflowStage[];
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
  read_failed?: boolean;
}

export interface CheckpointHistoryResponse {
  run_id: string;
  items: CheckpointProjection[];
  total: number;
}

export interface RunQuery {
  page: number;
  page_size: number;
  status?: RunStatus | '';
  status_group?: 'active' | 'ended';
  keyword?: string;
  from?: string;
  to?: string;
}

export interface PageResult<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  unreadable_runs?: number;
  degraded?: boolean;
  counts?: { active: number; ended: number; attention: number };
  attention_run_id?: string;
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
  | { type: 'thinking'; text?: string; redacted?: boolean }
  | {
      type: 'tool_use' | 'tool_decision';
      tool_use_id?: string | null;
      tool?: string;
      input?: Record<string, unknown>;
      elapsed_ms?: number;
      decision?: string;
      reason?: string;
      source_sha256?: string;
    }
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
  result: string | null;
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

export interface FileChange {
  path: string; available: boolean; reason?: string;
  before: string | null; after: string | null;
  before_exists?: boolean; after_exists?: boolean;
  status?: 'added' | 'deleted' | 'modified'; patch?: string;
}
export interface TaskChanges { available: boolean; reason?: string; files: FileChange[]; declared_files?: string[] }
