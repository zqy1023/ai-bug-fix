import type { TaskDetailResponse } from './types';

export function object(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

export function lines(value: unknown): string[] {
  return Array.isArray(value) ? value.map(text).filter(Boolean) : [];
}

/** Display recorded findings and edits; do not infer a repair from task completion. */
export function taskResults(detail: TaskDetailResponse) {
  const report = detail.task.report;
  const step = [...detail.task.steps].reverse().find(item => item.name === 'unified_fixer_agent');
  const fixer = object(step?.output);
  const value = (key: string) => report[key] ?? fixer[key];
  const plan = object(value('change_plan'));
  const files = lines(value('changed_files'));
  const rootCause = text(value('root_cause')) || text(fixer.analysis);
  const attribution = text(value('attribution_type'));
  const reviewStep = [...detail.task.steps].reverse().find(item => item.name === 'review_agent');
  const review = object(reviewStep?.output || report.review);
  const unresolved = lines(review.unresolved_issues ?? report.unresolved_issues);
  return {
    rootCause,
    summary: text(fixer.summary) || text(report.change_summary),
    attribution,
    skipReason: text(value('skip_reason')),
    unresolvedIssues: unresolved.length ? unresolved : !files.length && ['code', 'mixed'].includes(attribution) && rootCause ? [rootCause] : [],
    facts: lines(value('facts')),
    sources: (Array.isArray(value('source_evidence')) ? value('source_evidence') as unknown[] : []).map(object),
    gaps: lines(value('evidence_gaps')),
    rationale: text(plan.rationale),
    edits: (Array.isArray(plan.edits) ? plan.edits : []).map(object),
    files,
    analyzed: Boolean(step && step.status !== 'pending' && step.status !== 'running'),
    analysisFailed: step?.status === 'failed' || Boolean(text(fixer.failure_kind)),
    failureReason: text(fixer.blocked_reason) || text(step?.error?.message) || text(fixer.summary),
  };
}
