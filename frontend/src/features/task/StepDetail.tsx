import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Skeleton, Tabs } from 'antd';
import { FileTextOutlined } from '@ant-design/icons';
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
import { FAILED_RESULTS } from './stages';

function compactText(value: string, maxChars: number): string {
  const normalized = value.replace(/\\s+/g, ' ').trim();
  return normalized.length > maxChars
    ? `${normalized.slice(0, maxChars).trimEnd()}…`
    : normalized;
}

function ArtifactsPanel({
  runId, taskId, artifacts,
}: { runId: string; taskId: string; artifacts: ArtifactRef[] }) {
  const [openPath, setOpenPath] = useState('');
  const content = useQuery({
    queryKey: queryKeys.artifactContent(runId, openPath, taskId),
    queryFn: () => api.artifactContent(runId, openPath, taskId),
    enabled: Boolean(openPath),
  });

  if (!artifacts.length) {
    return <EmptyState title="该步骤没有产物引用" description="大体积输出会落在 artifacts 目录。" compact />;
  }

  return (
    <div className="artifacts-panel">
      <ul className="artifact-list">
        {artifacts.map((artifact) => (
          <li key={`${artifact.path}-${artifact.name}`}>
            <button
              type="button"
              className={'artifact-row' + (openPath === artifact.path ? ' is-active' : '')}
              onClick={() => setOpenPath(openPath === artifact.path ? '' : artifact.path)}
            >
              <FileTextOutlined />
              <span className="artifact-copy">
                <strong>{artifact.name}</strong>
                <code>{artifact.path}</code>
              </span>
              <span className="artifact-action">{openPath === artifact.path ? '收起' : '查看'}</span>
            </button>
          </li>
        ))}
      </ul>
      {openPath && (
        <div className="artifact-content">
          {content.isLoading && <Skeleton active paragraph={{ rows: 4 }} />}
          {content.isError && <EmptyState title="产物读取失败" compact />}
          {content.data && <JsonViewer value={content.data.content} defaultExpandDepth={1} />}
        </div>
      )}
    </div>
  );
}

export function StepDetail({
  runId, taskId, step, detail, runActive,
}: {
  runId: string; taskId: string; step: StepRecord;
  detail?: StepDetailResponse; runActive: boolean;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get('trace');
  const stepFailed = step.status === 'failed'
    || (step.result ? FAILED_RESULTS.has(String(step.result).toUpperCase()) : false);

  const items = [
    { key: 'input', label: '输入', children: <JsonViewer value={step.input ?? {}} /> },
    {
      key: 'output',
      label: '输出',
      children: step.status === 'running' || step.status === 'pending'
        ? <EmptyState title="输出尚未落盘" description="步骤仍在执行或尚未开始；完成后输出会写入步骤记录。" compact />
        : <JsonViewer value={step.output ?? {}} />,
    },
    {
      key: 'trace',
      label: 'Agent 追踪',
      children: (
        <AgentTrace
          runId={runId}
          taskId={taskId}
          stepName={step.name}
          attempt={step.attempt}
        />
      ),
    },
    {
      key: 'artifacts',
      label: `产物${step.artifacts?.length ? ` (${step.artifacts.length})` : ''}`,
      children: <ArtifactsPanel runId={runId} taskId={taskId} artifacts={step.artifacts ?? []} />,
    },
    {
      key: 'error',
      label: '结论 / 错误',
      children: (
        <div className="step-error-panel">
          <div className="step-result-block">
            <h4 className="conclusion-subhead">本步骤</h4>
            <div className="fact-row"><span>业务结论</span><strong>{step.result ?? '—'}</strong></div>
            {step.error ? (
              <div className="error-box">
                <strong>{step.error.code ?? 'STEP_EXECUTION_FAILED'}</strong>
                <p>{compactText(step.error.message ?? '没有错误详情', 360)}</p>
                {(step.error.message?.length ?? 0) > 360 && (
                  <details className="step-long-text">
                    <summary>查看完整错误（{step.error.message?.length ?? 0} 字符）</summary>
                    <pre>{step.error.message}</pre>
                  </details>
                )}
                <span>可重试：{step.error.retryable ? '是' : '否'}</span>
              </div>
            ) : stepFailed ? null : <EmptyState title="该步骤没有错误" description="失败步骤才会记录错误对象。" compact />}
          </div>
        </div>
      ),
    },
  ];

  const currentTab = tab ?? (stepFailed ? 'error' : 'output');

  return (
    <section className="step-detail">
      <header className="step-detail-head">
        <div className="step-detail-title">
          <span className="step-kicker">步骤 / {step.name}</span>
          <div>
            <h3>{step.title || step.name}</h3>
            {step.result && FAILED_RESULTS.has(String(step.result).toUpperCase()) ? (
              <>
                <StatusBadge kind="task" value={step.result} />
                <span className="step-exec-note">执行成功</span>
              </>
            ) : (
              <StatusBadge kind="step" value={step.status} />
            )}
            {step.attempt ? <span className="step-attempt">attempt {step.attempt}</span> : null}
            <span className="step-duration">
              {step.status === 'running' ? '执行中…' : formatDuration(step.duration_ms)}
            </span>
            {detail?.model_call_ids.length ? (
              <span className="step-duration">{detail.model_call_ids.length} 次模型调用</span>
            ) : null}
          </div>
        </div>
        {step.summary && (
          <div className="step-summary-block">
            <p className="step-summary">{compactText(step.summary, 220)}</p>
            {step.summary.length > 220 && (
              <details className="step-long-text">
                <summary>查看完整步骤摘要（{step.summary.length} 字符）</summary>
                <p>{step.summary}</p>
              </details>
            )}
          </div>
        )}
      </header>

      <Tabs
        activeKey={currentTab}
        onChange={(key) => {
          // 只切换详情 Tab，必须保留当前选中步骤；否则会回落到默认步骤，
          // 表现为“点任何步骤的输出都跳到另一个步骤”。
          const next = new URLSearchParams(searchParams);
          next.set('trace', key);
          next.set('step', step.name);
          setSearchParams(next, { replace: true });
        }}
        items={items}
        className="step-tabs"
      />

      <StepActionBar runId={runId} taskId={taskId} stepName={step.name} runActive={runActive} />
    </section>
  );
}
