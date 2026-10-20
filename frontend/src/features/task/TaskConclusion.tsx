import type { StepRecord, TaskDetailResponse } from '../../lib/types';
import { StatusBadge } from '../../components/StatusBadge';

export type AgentAnalysis = {
  step: StepRecord;
  rootCause: string;
  summary: string;
  category: string;
  scope: string;
  gaps: string[];
  facts: string[];
  findings: string[];
};

function text(output: Record<string, unknown>, key: string): string {
  const value = output[key];
  return typeof value === 'string' ? value.trim() : '';
}

function list(output: Record<string, unknown>, key: string): string[] {
  const value = output[key];
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : [];
}

/** 取最近一次定位/审查 Agent 的结论；locate 与 review 都可能产出分析。 */
export function analysisOf(steps: StepRecord[]): AgentAnalysis | undefined {
  const candidates = steps.filter(
    (step) => ['locate_agent', 'review_agent'].includes(step.name) && step.output,
  );
  const step = candidates[candidates.length - 1];
  if (!step?.output) return undefined;
  const output = step.output;
  return {
    step,
    rootCause: text(output, 'root_cause'),
    summary: text(output, 'summary'),
    category: text(output, 'category'),
    scope: text(output, 'recommended_scope'),
    gaps: list(output, 'evidence_gaps'),
    facts: list(output, 'facts'),
    findings: list(output, 'findings'),
  };
}

/**
 * 任务结论（一级信息，直接可见）：终态步骤 + 分析 Agent 的根因/摘要。
 * 失败原因与下一步在头部横幅已展示，这里不重复；长列表（发现/缺口/事实）
 * 默认折叠，只有结论性文字直接铺出。
 */
export function TaskConclusion({ task }: { task: TaskDetailResponse['task'] }) {
  const report = task.report;
  const analysis = analysisOf(task.steps ?? []);
  const result = String(task.result || '').toUpperCase();

  return (
    <section className="conclusion-card">
      <header className="conclusion-card-head">
        <h3>任务结论</h3>
        <StatusBadge kind="task" value={task.result} compact />
        {report?.terminal_step && <code>终态步骤 {report.terminal_step}</code>}
        {analysis && analysis.step.name !== report?.terminal_step && <code>{analysis.step.name}</code>}
      </header>

      {!analysis ? (
        <p className="conclusion-empty">
          {['RUNNING', 'PENDING', 'WAITING_APPROVAL'].includes(result)
            ? '分析 Agent 尚未产出结论（步骤可能仍在执行）。'
            : '该任务没有分析 Agent 结论（未进入定位或审查阶段）。'}
        </p>
      ) : (
        <>
          <div className="conclusion-fields">
            {analysis.rootCause && (
              <div className="conclusion-field">
                <span>根因</span>
                <p>{analysis.rootCause}</p>
              </div>
            )}
            {analysis.summary && (
              <div className="conclusion-field">
                <span>摘要</span>
                <p>{analysis.summary}</p>
              </div>
            )}
          </div>
          <div className="conclusion-tags">
            {analysis.category && <span className="conclusion-tag">category {analysis.category}</span>}
            {analysis.scope && <span className="conclusion-tag">建议范围 {analysis.scope}</span>}
          </div>
          {(analysis.findings.length > 0 || analysis.gaps.length > 0 || analysis.facts.length > 0) && (
            <div className="conclusion-drawers">
              {analysis.findings.length > 0 && (
                <details className="conclusion-details">
                  <summary>审查发现 ({analysis.findings.length})</summary>
                  <ul>{analysis.findings.map((item) => <li key={item}>{item}</li>)}</ul>
                </details>
              )}
              {analysis.gaps.length > 0 && (
                <details className="conclusion-details">
                  <summary>证据缺口 ({analysis.gaps.length})</summary>
                  <ul>{analysis.gaps.map((item) => <li key={item}>{item}</li>)}</ul>
                </details>
              )}
              {analysis.facts.length > 0 && (
                <details className="conclusion-details">
                  <summary>证据事实 ({analysis.facts.length})</summary>
                  <ul>{analysis.facts.map((item) => <li key={item}>{item}</li>)}</ul>
                </details>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}
