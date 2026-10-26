import type { ArtifactRef, Assessment, BusinessResult, ExecutionResult, StepRecord, StepStatus } from '../../lib/types';

/**
 * 审查 Agent 的内部工具。
 * 名单对应后端 app/workflow/definitions.py 的 REVIEW_EVIDENCE_STEPS；
 * 这些工具不是任务子图节点，正常情况下不出现在 steps.json 主表里。
 */
export const REVIEW_TOOL_NAMES = [
  'check_changes', 'syntax_regression', 'plan_validation',
  'verify_issue', 'run_regression', 'summarize_validation',
] as const;

export type ReviewToolName = (typeof REVIEW_TOOL_NAMES)[number];

/** review_agent 输出里的内部工具摘要；字段与后端 ReviewView.substeps 对应。 */
export interface ReviewSubstep {
  name: string;
  title: string;
  status: string;
  summary: string;
  executionStatus: string;
  assessmentOutcome: string;
  assessmentSummary: string;
  /** 内部工具完整记录在任务目录里的归档路径。 */
  recordPath: string;
}

/** 内部工具证据的可用性：已回传 / 已执行但未回传 / 未执行。 */
export type EvidenceState = 'available' | 'not_returned' | 'not_executed';

export const EVIDENCE_STATE_LABELS: Record<EvidenceState, string> = {
  available: '已回传',
  not_returned: '审查内部证据未回传',
  not_executed: '未执行',
};

const dict = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};

const text = (value: unknown): string => (typeof value === 'string' ? value : '');

/** 输出既可能直接挂在 output 上，也可能包在 output.payload 里。 */
function outputBody(output: unknown): Record<string, unknown> {
  const root = dict(output);
  const payload = dict(root.payload);
  return Object.keys(payload).length ? payload : root;
}

/** 读取 review_agent 输出中的内部工具清单；后端契约字段是 substeps。 */
export function reviewSubsteps(output: unknown): ReviewSubstep[] {
  const body = outputBody(output);
  const items = Array.isArray(body.substeps) ? body.substeps : [];
  return items.flatMap((item) => {
    const source = dict(item);
    const execution = dict(source.execution);
    const assessment = dict(source.assessment);
    const name = text(source.name);
    if (!name) return [];
    return [{
      name,
      title: text(source.title),
      status: text(source.status),
      summary: text(source.summary),
      executionStatus: text(execution.status),
      assessmentOutcome: text(assessment.outcome),
      assessmentSummary: text(assessment.summary),
      recordPath: text(source.record_path),
    }];
  });
}

/** 判断某项内部工具的证据是否可用；未回传与未执行必须分开表述。 */
export function evidenceState(step?: StepRecord, substep?: ReviewSubstep, reviewRan = false): EvidenceState {
  if (step) return 'available';
  // 内部工具摘要本身就是回传的结果：只有既没有状态也没有结论才算“有清单无数据”。
  if (substep && (substep.status || substep.assessmentOutcome || substep.summary)) return 'available';
  // 审查步骤已经跑过却没有给出这项结果，只能说“没回传”，不能说“没执行”。
  if (reviewRan) return 'not_returned';
  return 'not_executed';
}

const STEP_STATUSES: readonly string[] = ['pending', 'running', 'waiting', 'succeeded', 'failed', 'skipped'];
const EXECUTION_STATUSES: readonly string[] = ['succeeded', 'failed', 'waiting', 'cancelled'];
const ASSESSMENT_OUTCOMES: readonly string[] = ['passed', 'failed', 'inconclusive', 'not_applicable'];
const BUSINESS_RESULTS: readonly string[] = [
  'UNVERIFIED', 'BLOCKED', 'LOCATED', 'INSUFFICIENT_EVIDENCE', 'MANUAL', 'PASS', 'FAIL',
  'REJECTED', 'SKIPPED', 'DELIVERED', 'SUCCESS', 'FAILED',
];

function stepStatus(value: unknown): StepStatus {
  const raw = text(value);
  return (STEP_STATUSES as string[]).includes(raw) ? raw as StepStatus : 'pending';
}

function artifactRefs(value: unknown): ArtifactRef[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const source = dict(item);
    const name = text(source.name);
    const path = text(source.path);
    if (!name || !path) return [];
    const ref: ArtifactRef = { name, path };
    const mediaType = text(source.media_type);
    if (mediaType) ref.media_type = mediaType;
    const description = text(source.description);
    if (description) ref.description = description;
    return [ref];
  });
}

/**
 * 把内部工具摘要还原成步骤记录。
 * 后端把 6 个内部工具的结果放在 review_agent 输出的 substeps 里，
 * 字段与正式步骤记录同构，这里只映射已知字段。
 */
export function substepToRecord(substep?: ReviewSubstep): StepRecord | undefined {
  if (!substep || !substep.name) return undefined;
  const record: StepRecord = {
    name: substep.name,
    title: substep.title || substep.name,
    status: stepStatus(substep.status),
  };
  if (EXECUTION_STATUSES.includes(substep.executionStatus)) {
    record.execution = { status: substep.executionStatus as ExecutionResult['status'] };
  }
  if (ASSESSMENT_OUTCOMES.includes(substep.assessmentOutcome)) {
    record.assessment = {
      outcome: substep.assessmentOutcome as Assessment['outcome'],
      code: '',
      summary: substep.assessmentSummary,
    };
  }
  if (substep.summary) record.summary = substep.summary;
  return record;
}

/**
 * 把归档的内部工具记录还原成步骤记录。
 * 归档文件由后端写出，结构与正式步骤记录一致；这里只填充已知字段。
 */
export function toStepRecord(value: unknown): StepRecord | undefined {
  const body = dict(value);
  const name = text(body.name);
  if (!name) return undefined;
  const record: StepRecord = {
    name,
    title: text(body.title) || name,
    status: stepStatus(body.status),
  };
  const execution = dict(body.execution);
  const executionStatus = text(execution.status);
  if (EXECUTION_STATUSES.includes(executionStatus)) {
    record.execution = { status: executionStatus as ExecutionResult['status'] };
  }
  const assessment = dict(body.assessment);
  const outcome = text(assessment.outcome);
  if (ASSESSMENT_OUTCOMES.includes(outcome)) {
    record.assessment = {
      outcome: outcome as Assessment['outcome'],
      code: text(assessment.code),
      summary: text(assessment.summary),
    };
  }
  const result = text(body.result).toUpperCase();
  if (BUSINESS_RESULTS.includes(result)) record.result = result as BusinessResult;
  const summary = text(body.summary);
  if (summary) record.summary = summary;
  const output = dict(body.output);
  if (Object.keys(output).length) record.output = output;
  const input = dict(body.input);
  if (Object.keys(input).length) record.input = input;
  const artifacts = artifactRefs(body.artifacts);
  if (artifacts.length) record.artifacts = artifacts;
  const error = dict(body.error);
  if (text(error.message)) {
    record.error = {
      code: text(error.code) || undefined,
      message: text(error.message),
      retryable: error.retryable === true,
    };
  }
  const startedAt = text(body.started_at);
  if (startedAt) record.started_at = startedAt;
  const finishedAt = text(body.finished_at);
  if (finishedAt) record.finished_at = finishedAt;
  const duration = Number(body.duration_ms);
  if (Number.isFinite(duration)) record.duration_ms = duration;
  const attempt = Number(body.attempt);
  if (Number.isFinite(attempt)) record.attempt = attempt;
  return record;
}
