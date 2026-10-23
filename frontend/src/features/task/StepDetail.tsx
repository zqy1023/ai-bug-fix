import { useState } from 'react';
import { Button, Skeleton } from 'antd';
import { FileTextOutlined, BugOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { formatDuration } from '../../lib/format';
import type { ArtifactRef, StepDetailResponse, StepRecord } from '../../lib/types';
import { JsonViewer } from '../../components/JsonViewer';
import { EmptyState } from '../../components/EmptyState';
import { StatusBadge } from '../../components/StatusBadge';
import { AgentTrace } from '../agent/AgentTrace';
import { StepActionBar } from './StepActionBar';
import { outcomeText } from './StepOutcome';
import { FAILED_RESULTS } from './stages';

function ArtifactsPanel({ runId, taskId, artifacts }: { runId: string; taskId: string; artifacts: ArtifactRef[] }) {
  const [openPath, setOpenPath] = useState('');
  const content = useQuery({
    queryKey: queryKeys.artifactContent(runId, openPath, taskId),
    queryFn: () => api.artifactContent(runId, openPath, taskId),
    enabled: Boolean(openPath),
  });
  if (!artifacts.length) return <EmptyState title="暂无关联产物" description="完整报告和大体积输出保存在产物文件中。" compact />;
  return <div className="artifacts-panel">
    <ul className="artifact-list">{artifacts.slice(0, 8).map((artifact) => <li key={artifact.path}>
      <button type="button" className={'artifact-row' + (openPath === artifact.path ? ' is-active' : '')} onClick={() => setOpenPath(openPath === artifact.path ? '' : artifact.path)}>
        <FileTextOutlined /><span className="artifact-copy"><strong>{artifact.name}</strong><code>{artifact.path}</code></span><span className="artifact-action">{openPath === artifact.path ? '收起' : '查看'}</span>
      </button>
    </li>)}</ul>
    {openPath && <div className="artifact-content">
      {content.isLoading && <Skeleton active paragraph={{ rows: 3 }} />}
      {content.isError && <EmptyState title="产物读取失败" compact />}
      {content.data && <JsonViewer value={content.data.content} defaultExpandDepth={1} />}
    </div>}
  </div>;
}

export function StepDetail({ runId, taskId, step, detail, runActive, approvalPending = false }: {
  runId: string; taskId: string; step: StepRecord; detail?: StepDetailResponse; runActive: boolean; approvalPending?: boolean;
}) {
  const [showRaw, setShowRaw] = useState(false);
  const [showTrace, setShowTrace] = useState(false);
  step = detail?.step.name === step.name ? detail.step : step;
  const stepFailed = step.status === 'failed' || (step.result ? FAILED_RESULTS.has(String(step.result).toUpperCase()) : false);
  const raw = useQuery({
    queryKey: [...queryKeys.step(runId, taskId, step.name), 'raw'],
    queryFn: () => api.step(runId, taskId, step.name, true),
    enabled: showRaw,
    staleTime: 15_000,
  });
  const output = step.output ?? {};
  const outputRecord = output && typeof output === 'object' && !Array.isArray(output) ? output as Record<string, unknown> : {};
  const summary = String(outputRecord.root_cause || outputRecord.summary || outputRecord.reason || step.summary || outcomeText(step) || '').trim();
  const error = step.error?.message || '';
  const compactFields: Array<[string, unknown]> = [
    ['业务结论', outputRecord.verdict || outputRecord.status || step.result],
    ['修复分支', outputRecord.repair_branch],
    ['Commit', outputRecord.commit],
    ['实际变更文件', outputRecord.changed_files_count ?? (Array.isArray(outputRecord.changed_files) ? outputRecord.changed_files.length : undefined)],
  ].filter(([, value]) => value !== undefined && value !== null && value !== '');
  const artifacts = detail?.artifacts?.length ? detail.artifacts : step.artifacts ?? [];

  return <section className="step-detail step-detail-compact">
    <header className="step-detail-head">
      <div className="step-detail-title">
        <span className="step-kicker">执行节点</span>
        <h3>{step.title || step.name}</h3>
        <div className="step-meta-line">
          <StatusBadge kind={step.result && ['UNVERIFIED', 'MANUAL', 'INSUFFICIENT_EVIDENCE', 'FAIL', 'FAILED'].includes(String(step.result).toUpperCase()) ? 'task' : 'step'} value={step.result && ['UNVERIFIED', 'MANUAL', 'INSUFFICIENT_EVIDENCE', 'FAIL', 'FAILED'].includes(String(step.result).toUpperCase()) ? String(step.result) : step.status} stepName={step.name} />
          <span>{step.status === 'running' ? '执行中…' : formatDuration(step.duration_ms)}</span>
          {step.attempt ? <span>第 {step.attempt} 次执行</span> : null}
          {detail?.model_call_ids.length ? <span>{detail.model_call_ids.length} 次模型调用</span> : null}
        </div>
      </div>
    </header>

    <section className="step-summary-card">
      <div className="step-summary-label">{stepFailed ? '失败原因' : '执行摘要'}</div>
      <p>{error || summary || (step.status === 'pending' ? '该节点尚未执行。' : '该节点已记录状态，无需展开原始数据即可判断执行进度。')}</p>
      {error && step.error?.retryable !== undefined && <span className="step-retry-hint">节点重试：{step.error.retryable ? '可重试' : '不可重试'}</span>}
      {compactFields.length > 0 && <div className="step-key-fields">{compactFields.map(([label, value]) => <div className="overview-kv" key={label}><span>{label}</span><strong title={String(value)}>{String(value)}</strong></div>)}</div>}
    </section>

    <section className="step-detail-section">
      <header><h4>关联产物</h4><span>{artifacts.length} 项</span></header>
      <ArtifactsPanel runId={runId} taskId={taskId} artifacts={artifacts} />
    </section>

    <details className="step-advanced-diagnostics">
      <summary><BugOutlined /> 高级诊断（按需加载）</summary>
      <div className="step-advanced-actions">
        <Button size="small" onClick={() => setShowRaw((value) => !value)}>{showRaw ? '收起原始数据' : '查看原始输入 / 输出'}</Button>
        {step.name.endsWith('_agent') && <Button size="small" onClick={() => setShowTrace((value) => !value)}>{showTrace ? '收起 Agent 追踪' : '查看 Agent 追踪'}</Button>}
      </div>
      {showRaw && <div className="step-raw-data">
        {raw.isLoading && <Skeleton active paragraph={{ rows: 4 }} />}
        {raw.isError && <EmptyState title="原始数据加载失败" description="请重试加载。" compact />}
        {raw.data && <>
          <h5>原始输入</h5><JsonViewer value={raw.data.step.input ?? {}} defaultExpandDepth={1} />
          <h5>原始输出</h5><JsonViewer value={raw.data.step.output ?? {}} defaultExpandDepth={1} />
        </>}
      </div>}
      {showTrace && <AgentTrace runId={runId} taskId={taskId} stepName={step.name} attempt={step.attempt} />}
    </details>

    <StepActionBar runId={runId} taskId={taskId} stepName={step.name} stepStatus={stepFailed ? 'failed' : step.status} runActive={runActive} approvalPending={approvalPending} />
  </section>;
}
