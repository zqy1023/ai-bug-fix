export type StepStatus = 'pending' | 'running' | 'waiting' | 'succeeded' | 'failed' | 'skipped';
export type BusinessResult = 'passed' | 'failed' | 'rejected' | 'manual' | 'pending' | 'LOCATED' | 'INSUFFICIENT_EVIDENCE' | 'MANUAL' | 'PASS' | 'FAIL' | 'REJECTED' | 'SKIPPED' | 'DELIVERED' | 'SUCCESS' | 'FAILED' | null;
export type RunStatus = 'queued' | 'running' | 'waiting_approval' | 'succeeded' | 'partial_failed' | 'failed' | 'paused' | 'pause_requested' | 'cancelled' | 'cancel_requested';

export interface DashboardSummary {
  active_runs: number; waiting_approvals: number; succeeded_24h: number; failed_24h: number; success_rate_7d: number;
  recent_runs: RunSummary[]; daily_trend: Array<{ date: string; succeeded: number; failed: number }>;
}
export interface RunSummary {
  run_id: string; status: RunStatus; started_at: string; finished_at?: string | null; task_count: number;
  succeeded_tasks: number; failed_tasks: number; manual_tasks: number; waiting_tasks: number; operator?: string | null;
}
export interface StepRecord {
  name: string; title: string; status: StepStatus; result?: BusinessResult; started_at?: string | null; finished_at?: string | null;
  duration_ms?: number | null; attempt?: number; summary?: string; error?: { code?: string; message?: string; retryable?: boolean } | null;
  artifacts?: ArtifactRef[]; input?: Record<string, unknown>; output?: Record<string, unknown>;
}
export interface ArtifactRef { name: string; path: string; media_type?: string; size_bytes?: number; description?: string; task_id?: string; }
export interface WorkflowReport {
  report_version?: number; report_kind?: 'run_final' | 'task_final' | string; run_id?: string; task_id?: string;
  carrier?: string; biztype?: string; result?: string; status?: string; terminal_status?: string; terminal_step?: string;
  terminal_reason?: string; next_action?: string; finished_at?: string; elapsed_ms?: number; tokens?: number;
  problem?: string | Record<string, unknown>; root_cause?: string; change_summary?: string; completed_steps?: string[]; task_count?: number;
  task_results?: Array<{ task_id: string; carrier: string; biztype: string; result: string; terminal_step?: string; terminal_reason?: string; next_action?: string }>;
  legacy_report?: boolean;
}
export interface TaskSummary {
  task_id: string; carrier: string; biztype: string; result: string; terminal_status?: string; terminal_step?: string;
  terminal_reason?: string; next_action?: string; steps_count?: number; duration_ms?: number | null; approval_pending?: boolean;
  problem?: Record<string, unknown>; report?: WorkflowReport; steps?: StepRecord[];
}
export interface TaskDetailResponse {
  run_id: string; run_status: RunStatus; config: Record<string, unknown>;
  task: TaskSummary & { steps: StepRecord[]; report: WorkflowReport }; artifacts: ArtifactRef[];
}
export interface RunDetail extends RunSummary {
  run_error?: { status?: string; error_type?: string; message?: string; failed_at?: string; terminal_step?: string; terminal_reason?: string };
  run_report?: WorkflowReport; config: Record<string, unknown>; steps: StepRecord[]; tasks: TaskSummary[];
  checkpoint?: CheckpointProjection & { resumable: boolean }; artifacts: ArtifactRef[];
}
export interface CheckpointProjection {
  checkpoint_id?: string | null; parent_checkpoint_id?: string | null; created_at?: string | null;
  next_nodes: string[]; node?: string | null; has_interrupt?: boolean;
}
export interface CheckpointHistoryResponse { run_id: string; items: CheckpointProjection[]; total: number; }
export interface ApprovalItem {
  approval_id: string; run_id: string; task_id: string; carrier: string; biztype: string; scope: 'carrier' | 'project';
  status: 'pending' | 'approved' | 'rejected' | 'expired'; summary: string; changed_files: string[];
  change_plan?: { scope: 'carrier' | 'project'; rationale: string; requires_human_authorization: boolean; edits: Array<{ path: string; action: string; note: string }> };
  created_at: string; expires_at?: string | null;
}
export interface RunQuery { page: number; page_size: number; status?: RunStatus; keyword?: string; from?: string; to?: string; }
export interface PageResult<T> { items: T[]; total: number; page: number; page_size: number; }
export interface ModelCall {
  model_call_id: string; run_id: string; task_id: string; agent_role: string; attempt: number; provider?: string; model: string;
  prompt_version?: string; prompt_hash?: string; started_at: string; finished_at?: string; duration_ms?: number; status: string;
  input_tokens?: number | null; output_tokens?: number | null; cache_tokens?: number | null; total_tokens?: number | null;
  tool_call_count?: number | null; prompt_chars?: number | null; system_prompt_chars?: number | null; common_rules_chars?: number | null;
  role_prompt_chars?: number | null; task_context_chars?: number | null; problem_chars?: number | null; source_snapshot_chars?: number | null;
  evidence_chars?: number | null; identity_chars?: number | null; result_chars?: number | null; error_type?: string; event_count?: number;
  event_persistence_status?: "persisted" | "empty" | "failed"; events_path?: string;
  events?: Array<{ type: "assistant" | "tool_use" | "tool_result"; text?: string; tool?: string; input?: Record<string, unknown>; is_error?: boolean; elapsed_ms?: number }>;
  output_preview?: string;
}
export interface ControlAuditItem { id: number; run_id: string; task_id: string; action: string; actor: string; reason: string; created_at: string; }
export interface ApiErrorShape { code: string; message: string; request_id?: string; }
