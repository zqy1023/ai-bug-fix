/** 层级化 key：SSE 只失效当前 run 下的数据，不触发全站重取。 */
export const queryKeys = {
  health: ['workbench-health'] as const,
  runConfig: ['run-config'] as const,
  dashboard: ['dashboard'] as const,
  approvals: ['approvals'] as const,
  runs: (query: { page: number; status?: string; keyword?: string; from?: string; to?: string }) =>
    ['runs', query.page, query.status ?? '', query.keyword ?? '', query.from ?? '', query.to ?? ''] as const,
  runsRoot: ['runs'] as const,
  run: (runId: string) => ['run', runId] as const,
  board: (runId: string) => ['run-board', runId] as const,
  checkpoint: (runId: string) => ['checkpoint', runId] as const,
  checkpoints: (runId: string) => ['checkpoints', runId] as const,
  task: (runId: string, taskId: string) => ['task', runId, taskId] as const,
  step: (runId: string, taskId: string, stepName: string) => ['step', runId, taskId, stepName] as const,
  modelCalls: (runId: string, taskId?: string) => ['model-calls', runId, taskId ?? ''] as const,
  agentCall: (runId: string, taskId: string, callId: string) =>
    ['agent-call', runId, taskId, callId] as const,
  controlAudit: (runId: string) => ['control-audit', runId] as const,
  artifactContent: (runId: string, path?: string, taskId?: string) =>
    ['artifact-content', runId, taskId ?? '', path ?? ''] as const,
} as const;
